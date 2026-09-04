"""`/worktree on|off|merge|list`：給這個頻道一條專屬的 git 分支與工作目錄。

git 那一半整包沿用現成的 `wt_core`（不動它）。**跟舊 cc-bot 唯一的差別是狀態放哪**：
舊版把 `{path, branch, base, repo, prev_cwd}` 存進自己的 session 檔，engine 的
`ConvState` 沒有那個欄位，所以這裡改成——

  路徑    ＝ `ConvState.cwd` 本身（開＝換成 worktree 目錄並 persist，關＝換回 repo 根）
  開沒開  ＝ 從 cwd 反推（`detect`）：用 `wt_core.worktree_path()` 把「repo＋分支」重新
            組一次路徑，跟 cwd 對得上才算數。這樣就不必在這裡複製 `.cc-worktrees`
            這個常數，wt_core 改了規則這邊自動跟著走。
  看得見  ＝ 頻道名前綴 🌿（`ctx.sidebar.rename`）。

只有 `base`（從哪條分支長出來的）沒地方放：它不在路徑裡，也推不回來。存進 repo 的
`git config branch.<分支>.ccbase`——跟著分支走、重啟後還在、`git branch -d` 刪分支時
git 會連同整個 `branch.<名>` 區段一起清掉，不會留垃圾。讀不到時退回「主 repo 目前的
分支」並明講（`wt_merge` 的 repo_not_on_base 安全閘在那種情況下等於沒作用）。

安全閘一條都沒少，全部在 `wt_core` 裡：worktree 或主 repo 髒 → 不合併；主 repo 不在
base 上 → 不合併；合併衝突 → abort 還原並列出衝突檔名；`off` 不加 `--force`，有未提交
變更時 git 自己會拒絕。這裡只負責把失敗代碼翻成人話。

跟舊版的行為差異：`off`／`merge` 之後回到 **repo 根目錄**，不是「開 worktree 之前的
那個目錄」——舊版的 `prev_cwd` 沒地方存了。差別只在你原本停在 repo 的子目錄的情況。
"""
from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands

import wt_core

from ..i18n import t
from ..profile import conv_id as conv_of

if TYPE_CHECKING:
    from ..bootstrap import BotContext

# 頻道名前綴：這個頻道正掛在 worktree 上
WT_PREFIX = "🌿"

# base 存在 repo 的 git config 裡，鍵名放在分支自己的區段底下（刪分支時會被一起清掉）
_BASE_KEY = "ccbase"


@dataclass(frozen=True)
class WtInfo:
    """從 cwd 反推出來的 worktree 身分。"""

    path: Path
    repo: Path
    branch: str


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def read_base(repo: Path, branch: str) -> str | None:
    """這條 worktree 分支是從哪個分支長出來的。沒記過回 None。"""
    r = _git(["config", "--get", f"branch.{branch}.{_BASE_KEY}"], repo)
    return r.stdout.strip() or None if r.returncode == 0 else None


def write_base(repo: Path, branch: str, base: str) -> None:
    """把 base 記進分支的 config。失敗只是之後推不回來，不擋建立流程。"""
    _git(["config", f"branch.{branch}.{_BASE_KEY}", base], repo)


def detect(cwd: Path) -> WtInfo | None:
    """cwd 是不是 wt_core 開出來的 worktree？不是回 None。

    worktree 目錄名長成 `<repo 名>__<分支去斜線>`，且就放在 repo 同層的集中目錄底下，
    所以 repo 根 ＝ `cwd.parent.parent / cwd.name 前半段`。組回去對得上才承認——
    純粹路徑長得像、但 git 說它在別條分支上的目錄不會被誤判。
    """
    name = cwd.name
    if "__" not in name:
        return None
    repo = cwd.parent.parent / name.split("__", 1)[0]
    if not repo.is_dir():
        return None
    branch = wt_core.current_branch(cwd)
    if not branch or wt_core.worktree_path(repo, branch) != cwd:
        return None
    return WtInfo(path=cwd, repo=repo, branch=branch)


def strip_prefix(name: str) -> str:
    """去掉頻道名的 🌿 前綴。Discord 會把空白換成 `-`，所以連分隔符一起吃掉。"""
    s = (name or "").lstrip()
    if s.startswith(WT_PREFIX):
        s = s[len(WT_PREFIX):]
    return s.lstrip("-_ ").strip()


def _err_text(error: str) -> str:
    """wt_core 的錯誤代碼 → 人話。認不得的就當 git 原始訊息呈現。"""
    return {
        "not_a_repo": t("wt_err_not_repo"),
        "no_base_branch": t("wt_err_no_base"),
        "path_exists": t("wt_err_path_exists"),
    }.get(error, t("wt_err_git", err=error[:300]))


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth

    async def _mark(inter: discord.Interaction, on: bool) -> None:
        """頻道名的 🌿 前綴加／去。純視覺，失敗由 sidebar.rename 自己吞掉。

        比對的是「現在有沒有前綴」而不是完整名字：Discord 會把 `🌿 名字` 正規化成
        `🌿-名字`，拿完整名字比會每次都判定不同、每次都白改一次名。
        """
        ch = inter.channel
        cur = getattr(ch, "name", "") or ""
        if bool(cur.lstrip().startswith(WT_PREFIX)) == on or ctx.sidebar is None:
            return
        base = strip_prefix(cur)
        if not base:
            return
        await ctx.sidebar.rename(inter.channel_id, f"{WT_PREFIX} {base}" if on else base)

    @tree.command(name="worktree", description=t("cmd_worktree_desc"))
    @app_commands.choices(action=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="merge", value="merge"),
        app_commands.Choice(name="off", value="off"),
        app_commands.Choice(name="list", value="list"),
    ])
    async def cmd_worktree(inter: discord.Interaction, action: str,
                           name: str | None = None) -> None:
        if not await auth.check(inter):
            return
        # git 全是阻塞呼叫（to_thread），一律先 defer
        await inter.response.defer()
        from engine import state as state_mod
        from engine.state import get_state
        st = get_state(conv_of(inter.channel_id))
        cwd = Path(st.cwd)
        info = await asyncio.to_thread(detect, cwd)

        if action == "list":
            items = await asyncio.to_thread(wt_core.list_worktrees, cwd)
            if not items:
                await inter.followup.send(t("wt_list_none"))
                return
            lines = [t("wt_list_title")]
            for it in items:
                br = it.get("branch") or (it.get("head", "")[:8]) or "?"
                lines.append(t("wt_list_item", branch=br, path=it.get("path", "")))
            await inter.followup.send("\n".join(lines))
            return

        if action == "on":
            if info is not None:
                await inter.followup.send(
                    t("wt_already_on", branch=info.branch, path=info.path))
                return
            seg = name or strip_prefix(getattr(inter.channel, "name", "") or "") or "session"
            res = await asyncio.to_thread(wt_core.create, cwd, seg)
            if not res.ok:
                await inter.followup.send(_err_text(res.error))
                return
            await asyncio.to_thread(write_base, res.repo, res.branch, res.base)
            st.cwd = res.path
            state_mod.persist(st)     # cwd 在 client 指紋裡，下一回合自動重建 client
            await inter.followup.send(
                t("wt_on_done", branch=res.branch, base=res.base, path=res.path))
            await _mark(inter, True)
            return

        if info is None:              # merge / off 都得先在 worktree 上
            await inter.followup.send(t("wt_not_on"))
            return
        home = info.repo if info.repo.is_dir() else ctx.settings.default_cwd

        if action == "merge":
            base = await asyncio.to_thread(read_base, info.repo, info.branch)
            if base is None:
                # 舊 bot 開的 worktree 沒記過 base。退回「主 repo 現在停的分支」，
                # 但那等於讓 wt_core 的 repo_not_on_base 安全閘失效，要講明白
                base = await asyncio.to_thread(wt_core.current_branch, info.repo)
                if not base:
                    await inter.followup.send(t("wt_err_no_base"))
                    return
                await inter.followup.send(t("wt_base_guessed", base=base))
            res = await asyncio.to_thread(
                wt_core.merge, info.repo, info.path, info.branch, base)
            if not res.ok:
                # 任何一關沒過都保持原狀、不刪任何東西
                if res.error == "worktree_dirty":
                    await inter.followup.send(t("wt_merge_wt_dirty"))
                elif res.error == "repo_dirty":
                    await inter.followup.send(t("wt_merge_repo_dirty", base=base))
                elif res.error.startswith("repo_not_on_base"):
                    cur = res.error.split(":", 1)[1] if ":" in res.error else "?"
                    await inter.followup.send(t("wt_merge_not_on_base", base=base, cur=cur))
                elif res.error == "merge_conflict":
                    await inter.followup.send(t("wt_merge_conflict",
                                                branch=info.branch, base=base,
                                                files=(res.detail or "?")[:500]))
                else:
                    await inter.followup.send(_err_text(res.error))
                return
            st.cwd = home
            state_mod.persist(st)
            await inter.followup.send(
                t("wt_merge_done", branch=info.branch, base=base, cwd=home))
            await _mark(inter, False)
            return

        # action == "off"：不加 --force，有未提交變更時 git 自己會拒絕，工作保住
        res = await asyncio.to_thread(wt_core.remove, info.repo, info.path)
        if not res.ok:
            await inter.followup.send(t("wt_off_dirty", err=res.error[:300]))
            return
        st.cwd = home
        state_mod.persist(st)
        await inter.followup.send(t("wt_off_done", branch=info.branch, cwd=home))
        await _mark(inter, False)
