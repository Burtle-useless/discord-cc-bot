"""git worktree 平行協作核心邏輯。

與 Discord 無關的純函式，方便獨立測試。
概念：一個 Discord 頻道 ↔ 一個 worktree（獨立分支 + 獨立工作目錄），
讓多個頻道能同時在同一個 repo 上互不干擾地改檔，最後再各自合併回主分支。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WTResult:
    """worktree 操作結果。ok 為 False 時看 error 取得失敗原因（代碼或 git 訊息）。"""
    ok: bool
    path: Path | None = None
    branch: str | None = None
    base: str | None = None
    repo: Path | None = None
    error: str = ""
    detail: str = ""  # 額外可讀資訊（如合併衝突的檔名清單）


def _git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess[str]:
    """執行 git 子指令並回傳結果；不丟例外，由呼叫端依 returncode 判斷成敗。"""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def repo_root(cwd: str | Path) -> Path | None:
    """回傳 cwd 所在 git repo 的根目錄；非 git repo 回傳 None。"""
    r = _git(["rev-parse", "--show-toplevel"], cwd)
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return Path(out) if out else None


def current_branch(cwd: str | Path) -> str | None:
    """回傳目前分支名；偵測失敗或 detached HEAD 回傳 None。"""
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if r.returncode != 0:
        return None
    name = r.stdout.strip()
    return name if name and name != "HEAD" else None


def is_clean(cwd: str | Path) -> bool:
    """工作目錄是否乾淨（無未提交變更、無未追蹤檔案）。"""
    r = _git(["status", "--porcelain"], cwd)
    return r.returncode == 0 and not r.stdout.strip()


def branch_exists(repo: str | Path, branch: str) -> bool:
    """指定本地分支是否已存在。"""
    r = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo)
    return r.returncode == 0


_INVALID_SEG = re.compile(r"[^0-9A-Za-z_-]+")


def safe_branch_segment(name: str) -> str:
    """把任意字串（如頻道名）轉成合法的 git 分支名片段。"""
    s = _INVALID_SEG.sub("-", name).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s.lower() or "session"


def worktree_base(repo: Path) -> Path:
    """worktree 集中存放目錄：放在 repo 同層的 .cc-worktrees/。

    刻意放在 repo「外」（同層），避免 worktree 內容被 repo 本身掃描或誤提交。
    """
    return repo.parent / ".cc-worktrees"


def worktree_path(repo: Path, branch: str) -> Path:
    """某分支對應的 worktree 目錄路徑。"""
    safe = branch.replace("/", "-")
    return worktree_base(repo) / f"{repo.name}__{safe}"


def create(cwd: str | Path, segment: str) -> WTResult:
    """在目前 repo 開一個 worktree（分支 cc/<segment>）。

    branch 已存在 → 直接掛上該分支；不存在 → 從目前分支新建。
    """
    repo = repo_root(cwd)
    if repo is None:
        return WTResult(ok=False, error="not_a_repo")
    base = current_branch(repo)
    if base is None:
        return WTResult(ok=False, error="no_base_branch")
    branch = f"cc/{safe_branch_segment(segment)}"
    path = worktree_path(repo, branch)
    if path.exists():
        return WTResult(ok=False, error="path_exists", path=path, branch=branch)
    worktree_base(repo).mkdir(parents=True, exist_ok=True)
    if branch_exists(repo, branch):
        # 分支已存在（例如先前 off 後留下的）→ 重新掛上，不動其內容
        r = _git(["worktree", "add", str(path), branch], repo)
    else:
        r = _git(["worktree", "add", str(path), "-b", branch, base], repo)
    if r.returncode != 0:
        return WTResult(ok=False, error=(r.stderr.strip() or r.stdout.strip()))
    return WTResult(ok=True, path=path, branch=branch, base=base, repo=repo)


def remove(repo: str | Path, path: str | Path) -> WTResult:
    """移除 worktree。

    刻意不加 --force：有未提交變更或未追蹤檔案時 git 會自動拒絕，
    形成天然的安全閘（資料不會被默默丟掉）。
    """
    r = _git(["worktree", "remove", str(path)], repo)
    if r.returncode != 0:
        return WTResult(ok=False, error=(r.stderr.strip() or r.stdout.strip()))
    return WTResult(ok=True)


def merge(repo: str | Path, path: str | Path, branch: str, base: str) -> WTResult:
    """把 worktree 的分支合併回 base，成功後自動清理（移除 worktree + 刪分支）。

    硬安全閘（Q3-A）：只有「worktree 與主 repo 都乾淨、且合併無衝突」時才會
    清理；任何一關沒過就保持原狀、不刪任何東西，未提交的工作絕不會被丟掉。
    """
    repo_p = Path(repo)
    # 1. worktree 必須全部提交（未提交的工作不可被默默併掉或丟棄）
    if not is_clean(path):
        return WTResult(ok=False, error="worktree_dirty",
                        branch=branch, base=base, repo=repo_p)
    # 2. 主 repo 也必須乾淨，避免合併動到它未提交的變更
    if not is_clean(repo_p):
        return WTResult(ok=False, error="repo_dirty",
                        branch=branch, base=base, repo=repo_p)
    # 3. 合併是合進「目前分支」，故主 repo 必須正停在 base 上
    cur = current_branch(repo_p)
    if cur != base:
        return WTResult(ok=False, error=f"repo_not_on_base:{cur or 'detached'}",
                        branch=branch, base=base, repo=repo_p)
    # 4. 執行合併；--no-edit 用預設訊息，避免在非互動環境叫出編輯器卡住
    r = _git(["merge", "--no-edit", branch], repo_p)
    if r.returncode != 0:
        # 衝突 → 先抓出未合併（U）的檔名，再 abort 還原，主 repo 保持不變
        files = _git(["diff", "--name-only", "--diff-filter=U"], repo_p).stdout.strip()
        _git(["merge", "--abort"], repo_p)
        return WTResult(ok=False, error="merge_conflict", detail=files,
                        branch=branch, base=base, repo=repo_p)
    # 5. 合併成功 → 移除 worktree（仍不加 --force；前面已確認乾淨故會過）
    rm = remove(repo_p, path)
    if not rm.ok:
        return WTResult(ok=False, error=rm.error,
                        branch=branch, base=base, repo=repo_p)
    # 6. 刪分支用 -d（只刪「已完全合併」的分支；萬一沒合乾淨會自動拒絕）
    _git(["branch", "-d", branch], repo_p)
    return WTResult(ok=True, branch=branch, base=base, repo=repo_p)


def list_worktrees(cwd: str | Path) -> list[dict[str, str]]:
    """列出目前 repo 的所有 worktree（解析 porcelain 格式）。"""
    repo = repo_root(cwd)
    if repo is None:
        return []
    r = _git(["worktree", "list", "--porcelain"], repo)
    if r.returncode != 0:
        return []
    items: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if not line.strip():
            if cur:
                items.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):].replace("refs/heads/", "")
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.strip() == "detached":
            cur["detached"] = "1"
    if cur:
        items.append(cur)
    return items
