"""wt_core 的整合測試：用臨時 git repo 跑完整 worktree 生命週期。

執行：python test_wt_core.py
不依賴任何測試框架，全程用 assert，最後印出結果並清理臨時目錄。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import wt_core as wt

# Windows 主控台預設 cp950，統一改 utf-8 避免印中文/符號時編碼錯誤
sys.stdout.reconfigure(encoding="utf-8")


def _run(args: list[str], cwd: Path) -> None:
    """跑一個必須成功的 git 指令（測試前置用）。"""
    r = subprocess.run(["git", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"git {args} 失敗：{r.stderr}"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="wt_test_"))
    repo = tmp / "myrepo"
    repo.mkdir()
    try:
        # 1. 初始化一個有首次提交的 repo（worktree 需要至少一個 commit）
        _run(["init", "-b", "main"], repo)
        _run(["config", "user.email", "test@example.com"], repo)
        _run(["config", "user.name", "tester"], repo)
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        _run(["add", "."], repo)
        _run(["commit", "-m", "init"], repo)

        # 2. repo 偵測
        assert wt.repo_root(repo) is not None, "應偵測為 git repo"
        assert wt.repo_root(tmp) is None, "tmp 不是 git repo 應回 None"
        assert wt.current_branch(repo) == "main", "目前分支應為 main"
        assert wt.is_clean(repo) is True, "初始 repo 應乾淨"

        # 3. 分支名清洗
        assert wt.safe_branch_segment("一般-Channel #2") == "channel-2", \
            f"清洗結果非預期：{wt.safe_branch_segment('一般-Channel #2')}"

        # 4. create：開 worktree
        res = wt.create(repo, "feature-x")
        assert res.ok, f"create 失敗：{res.error}"
        assert res.branch == "cc/feature-x", f"分支名非預期：{res.branch}"
        assert res.base == "main"
        assert res.path is not None and res.path.is_dir(), "worktree 目錄應存在"
        # worktree 必須在 repo「外」（同層 .cc-worktrees）
        assert ".cc-worktrees" in str(res.path), "worktree 應放在 .cc-worktrees"
        assert wt.current_branch(res.path) == "cc/feature-x", "worktree 應在新分支上"

        # 5. 重複 create 同名 → 應被 path_exists 擋下
        dup = wt.create(repo, "feature-x")
        assert not dup.ok and dup.error == "path_exists", "重複開同名應被擋"

        # 6. list：應看到 main 與 cc/feature-x 兩個 worktree
        items = wt.list_worktrees(repo)
        branches = {i.get("branch") for i in items}
        assert "main" in branches and "cc/feature-x" in branches, \
            f"list 結果非預期：{branches}"

        # 7. 在 worktree 內改檔 → 變髒 → 安全閘：remove 應被拒絕
        wt_path = res.path
        (wt_path / "work.txt").write_text("wip\n", encoding="utf-8")
        assert wt.is_clean(wt_path) is False, "改檔後 worktree 應為髒"
        blocked = wt.remove(repo, wt_path)
        assert not blocked.ok, "有未提交變更時 remove 應被拒絕（安全閘）"
        assert wt_path.is_dir(), "被拒絕後 worktree 應仍在"

        # 8. 提交後變乾淨 → remove 應成功
        _run(["add", "."], wt_path)
        _run(["commit", "-m", "wip done"], wt_path)
        assert wt.is_clean(wt_path) is True, "提交後 worktree 應乾淨"
        ok = wt.remove(repo, wt_path)
        assert ok.ok, f"乾淨時 remove 應成功：{ok.error}"
        assert not wt_path.exists(), "remove 後 worktree 目錄應消失"

        # 9. 分支仍在（off 不刪分支，工作保留）
        assert wt.branch_exists(repo, "cc/feature-x"), "off 後分支應保留"

        # 10. 分支已存在時 create → 重新掛上，不報錯
        again = wt.create(repo, "feature-x")
        assert again.ok, f"重新掛既有分支應成功：{again.error}"
        assert again.path is not None and again.path.is_dir()

        # 11. merge（happy path）：把 cc/feature-x 合回 main 並自動清理
        m = wt.merge(repo, again.path, again.branch, again.base)
        assert m.ok, f"merge 應成功：{m.error}"
        assert not again.path.exists(), "merge 後 worktree 目錄應消失"
        assert not wt.branch_exists(repo, "cc/feature-x"), "merge 後分支應被刪除"
        assert (repo / "work.txt").read_text(encoding="utf-8") == "wip\n", \
            "work.txt 應已合併進 main"

        # 12. merge 衝突：兩邊改同一檔 → 應中止、什麼都不動、worktree/分支保留
        conf = wt.create(repo, "conf")
        assert conf.ok, f"建立 conf worktree 應成功：{conf.error}"
        (conf.path / "README.md").write_text("from-worktree\n", encoding="utf-8")
        _run(["add", "."], conf.path)
        _run(["commit", "-m", "wt edit readme"], conf.path)
        # 在主 repo 直接改同一檔並提交，製造分歧
        (repo / "README.md").write_text("from-main\n", encoding="utf-8")
        _run(["add", "."], repo)
        _run(["commit", "-m", "main edit readme"], repo)
        mc = wt.merge(repo, conf.path, conf.branch, conf.base)
        assert not mc.ok and mc.error == "merge_conflict", f"應回報衝突：{mc.error}"
        assert "README.md" in mc.detail, f"衝突檔清單應含 README.md：{mc.detail}"
        assert (repo / "README.md").read_text(encoding="utf-8") == "from-main\n", \
            "衝突中止後 main 應維持自己的版本"
        assert wt.is_clean(repo), "衝突中止後 main 應乾淨"
        assert conf.path.is_dir(), "衝突後 worktree 應保留"
        assert wt.branch_exists(repo, "cc/conf"), "衝突後分支應保留"

        # 13. 安全閘：worktree 有未提交變更 → merge 應被拒絕，原封不動
        d = wt.create(repo, "dirtywt")
        assert d.ok, f"建立 dirtywt worktree 應成功：{d.error}"
        (d.path / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")
        md = wt.merge(repo, d.path, d.branch, d.base)
        assert not md.ok and md.error == "worktree_dirty", f"髒 worktree 應拒絕：{md.error}"
        assert d.path.is_dir() and wt.branch_exists(repo, "cc/dirtywt"), \
            "拒絕後 worktree 與分支應原封不動"

        # 14. 安全閘：主 repo 有未提交變更 → merge 應被拒絕
        _run(["add", "."], d.path)
        _run(["commit", "-m", "commit scratch"], d.path)
        assert wt.is_clean(d.path), "提交後 dirtywt 應乾淨"
        (repo / "dirt.txt").write_text("main dirty\n", encoding="utf-8")  # 弄髒主 repo
        mr = wt.merge(repo, d.path, d.branch, d.base)
        assert not mr.ok and mr.error == "repo_dirty", f"髒主 repo 應拒絕：{mr.error}"
        (repo / "dirt.txt").unlink()  # 還原，避免影響清理

        print("✅ 全部通過（14 項）")
    finally:
        # 清理：先嘗試移除所有 worktree，再刪臨時目錄
        subprocess.run(["git", "worktree", "prune"], cwd=str(repo),
                       capture_output=True, text=True)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
