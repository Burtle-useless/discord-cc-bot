"""基本指令：help、guide、stop、status、continue、rename、cd、pwd。"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from engine import state as state_mod
from engine import titles
from engine.state import eff_effort, eff_model, get_state
from engine.turn import ctx_limit

from ..i18n import t
from ..profile import conv_id as conv_of

if TYPE_CHECKING:
    from ..bootstrap import BotContext

GUIDE_KEYS = ("basics", "sessions", "model", "files", "safety",
              "schedule", "worktree", "voice")


def _bar(pct: float, w: int = 14) -> str:
    n = max(0, min(w, int(round(pct / 100 * w))))
    return "█" * n + "░" * (w - n)


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth
    worker = ctx.worker

    @tree.command(name="help", description=t("cmd_help_desc"))
    async def cmd_help(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        await inter.response.send_message(t("help_text"))

    @tree.command(name="guide", description=t("cmd_guide_desc"))
    @app_commands.choices(topic=[
        app_commands.Choice(name=t(f"guide_topic_{k}"), value=k) for k in GUIDE_KEYS
    ])
    async def cmd_guide(inter: discord.Interaction, topic: str | None = None) -> None:
        if not await auth.check(inter):
            return
        key = f"guide_{topic}" if topic in GUIDE_KEYS else "guide_overview"
        await inter.response.send_message(t(key), ephemeral=True)

    @tree.command(name="stop", description=t("cmd_stop_desc"))
    async def cmd_stop(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        if worker.stop(conv_of(inter.channel_id)):
            await inter.response.send_message(t("stop_sent"))
        else:
            await inter.response.send_message(t("stop_nothing"), ephemeral=True)

    @tree.command(name="continue", description=t("cmd_continue_desc"))
    async def cmd_continue(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        st = get_state(conv_of(inter.channel_id))
        if st.session_id:
            await inter.response.send_message(t("continue_resume", id=st.session_id[:8]))
        else:
            await inter.response.send_message(t("continue_none"))

    @tree.command(name="status", description=t("cmd_status_desc"))
    async def cmd_status(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        conv = conv_of(inter.channel_id)
        st = get_state(conv)
        limit = ctx_limit(st)
        pct = st.ctx_tokens / limit * 100 if limit else 0.0
        lines = [
            t("status_title"),
            t("status_convo", label=state_mod.get_title(conv) or t("untitled")),
            t("status_dir", cwd=st.cwd),
            t("status_session", id=st.session_id[:8]) if st.session_id else t("status_session_none"),
            t("status_model", model=eff_model(st)),
            t("status_effort", effort=eff_effort(st) or t("default_inline")),
            t("status_context", bar=_bar(pct), ctx=f"{st.ctx_tokens:,}", limit=f"{limit:,}"),
            t("status_running", n=len(worker.pending_of(conv))) if worker.is_running(conv)
            else t("status_idle"),
        ]
        await inter.response.send_message("\n".join(lines))

    @tree.command(name="rename", description=t("cmd_rename_desc"))
    async def cmd_rename(inter: discord.Interaction, name: str | None = None) -> None:
        if not await auth.check(inter):
            return
        conv = conv_of(inter.channel_id)
        st = get_state(conv)
        if not st.session_id and not (name and name.strip()):
            await inter.response.send_message(t("rename_no_session"), ephemeral=True)
            return
        await inter.response.defer()
        if name and name.strip():
            title = name.strip()[:30]
            state_mod.set_title(conv, title)
            await ctx.sidebar.rename(inter.channel_id, title)
        else:
            # 讀 session 內容請 Haiku 取名；apply_title 會發 conv_renamed 狀態事件，
            # frontend 收到就改頻道名，跟自動命名走同一條路
            fe = ctx.frontend_for(conv)
            ok = await titles.apply_title(conv, titles.title_source(conv, ""), fe)
            if not ok:
                await inter.followup.send(t("rename_gen_failed"))
                return
            title = state_mod.get_title(conv) or ""
        await inter.followup.send(t("renamed", title=title))

    @tree.command(name="cd", description=t("cmd_cd_desc"))
    async def cmd_cd(inter: discord.Interaction, path: str) -> None:
        if not await auth.check(inter):
            return
        p = Path(path)
        if not p.is_dir():
            await inter.response.send_message(t("cd_not_found", path=path))
            return
        st = get_state(conv_of(inter.channel_id))
        st.cwd = p
        state_mod.persist(st)       # cwd 在 client 指紋裡，下一回合自動重建 client
        await inter.response.send_message(t("cd_done", p=p))

    @tree.command(name="pwd", description=t("cmd_pwd_desc"))
    async def cmd_pwd(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        st = get_state(conv_of(inter.channel_id))
        # 在 git 目錄裡就順便報分支：開著 worktree 時「我現在在哪條線上」比路徑更重要
        branch = ""
        try:
            import wt_core
            branch = wt_core.current_branch(st.cwd) or ""
        except Exception:  # noqa: BLE001 — 沒有 git、不是 repo，都只是沒有分支可報
            branch = ""
        msg = f"📂 `{st.cwd}`"
        if branch:
            msg += t("pwd_branch", branch=branch)
        await inter.response.send_message(msg)
