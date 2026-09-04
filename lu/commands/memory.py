"""記憶類指令：/search（語意搜尋歷史對話）、/recall（原話核對）、/handoff（交接稿）。

三個都在處理同一件事的不同面向：**這段對話到底發生過什麼**。
  /search  找回以前某一段對話（語意，不是關鍵字）
  /recall  拿兩份第一手紀錄回頭核對「使用者說過什麼」，抓模型自己的記憶幻覺
  /handoff 把這段對話濃縮成另一台電腦貼上就能接手的獨立文件

`/recall` 是 cc-bot 這個前端獨有、引擎那邊沒有的東西：對話會被壓縮，模型事後轉述
使用者說過的話可能是錯的，而且聽起來很像真的。帳本與 Discord 頻道歷史都不會被
壓縮，拿它們對照才問得出真相。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from engine import meta
from engine import sessions as sess_mod
from engine import titles
from engine.state import eff_model, get_state

from .. import ledger, semantic
from ..frontend import send_long
from ..i18n import t
from ..profile import conv_id as conv_of

if TYPE_CHECKING:
    from ..bootstrap import BotContext

log = logging.getLogger(__name__)

# 交接稿餵給模型的對話長度上限。太短寫不出脈絡，太長把成本與延遲拉高，
# 5 萬字是舊 cc-bot 用了三個月的值。
HANDOFF_CHARS = 50_000
RECALL_DEFAULT = 30


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth

    @tree.command(name="search", description=t("cmd_search_desc"))
    @app_commands.describe(q=t("search_q_desc"))
    async def cmd_search(inter: discord.Interaction, q: str) -> None:
        if not await auth.check(inter):
            return
        query = q.strip()
        if not query:
            await inter.response.send_message(t("search_empty"), ephemeral=True)
            return
        await inter.response.defer(ephemeral=True)
        try:
            hits, mode = await asyncio.to_thread(semantic.search, query, 25)
        except Exception:  # noqa: BLE001
            log.exception("搜尋失敗")
            await inter.followup.send(t("search_failed"), ephemeral=True)
            return
        if not hits:
            await inter.followup.send(t("search_none", q=query), ephemeral=True)
            return
        from .sessions import SessionsView
        view = SessionsView(ctx, inter.user.id, "")
        view.set_entries([{
            "session_id": h["session_id"], "title": h.get("title") or t("untitled"),
            "cwd": h.get("cwd") or "", "mtime": h.get("mtime") or 0,
            "snippet": h.get("snippet") or "",
        } for h in hits])
        head = t("search_head", q=query, n=len(hits),
                 mode=t("search_mode_semantic") if mode == "semantic" else t("search_mode_literal"))
        await inter.followup.send(head + "\n" + view.text(), view=view, ephemeral=True)

    @tree.command(name="recall", description=t("cmd_recall_desc"))
    @app_commands.describe(count=t("recall_count_desc"))
    async def cmd_recall(inter: discord.Interaction, count: int = RECALL_DEFAULT) -> None:
        if not await auth.check(inter):
            return
        count = max(5, min(100, count))
        conv = conv_of(inter.channel_id)
        rows = ledger.recent(ctx.settings.data_dir, inter.channel_id, count)
        history = await _channel_history(inter.channel, count)
        if not rows and not history:
            await inter.response.send_message(t("recall_nothing"), ephemeral=True)
            return
        prompt = t("recall_prompt",
                   ledger="\n".join(rows) or t("recall_no_ledger"),
                   history=history or t("recall_no_history"))
        await inter.response.send_message(t("recall_sent", n=len(rows)))
        # 走 worker：跟一般訊息同一條佇列，忙碌中會排隊、也停得掉。
        # 自己另外開一輪會變成兩個回合同時讀同一個 session。
        await ctx.worker.submit(conv, prompt, "指令")

    @tree.command(name="handoff", description=t("cmd_handoff_desc"))
    async def cmd_handoff(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        conv = conv_of(inter.channel_id)
        st = get_state(conv)
        if not st.session_id:
            await inter.response.send_message(t("handoff_empty"), ephemeral=True)
            return
        await inter.response.defer()
        await inter.followup.send(t("handoff_generating"))
        text = await asyncio.to_thread(
            sess_mod.session_text, st.session_id, HANDOFF_CHARS, "both")
        if not text:
            await inter.followup.send(t("handoff_empty"))
            return
        try:
            # 用這條對話自己的模型而不是 Haiku：交接稿寫壞了，另一台電腦是照著
            # 錯的脈絡繼續做，比多花一點成本貴得多
            doc = (await meta.ask_once(t("handoff_prompt") + "\n\n" + text,
                                       eff_model(st))).strip()
        except Exception:  # noqa: BLE001
            log.exception("生交接稿失敗")
            await inter.followup.send(t("handoff_failed"))
            return
        if not doc:
            await inter.followup.send(t("handoff_empty"))
            return
        # send_long：短的貼成訊息（可直接複製）、長的存成 .md 上傳
        await send_long(inter.channel, t("handoff_caption") + "\n\n" + doc,
                        tmp_dir=ctx.settings.data_dir / "tmp")


async def _channel_history(channel: discord.abc.Messageable, count: int) -> str:
    """頻道最近 count 則訊息，舊到新，一行一則。抓不到就回空字串。"""
    try:
        rows = [m async for m in channel.history(limit=count)]
    except Exception:  # noqa: BLE001 — 少一份紀錄就少一份，不要讓整個指令失敗
        log.exception("讀頻道歷史失敗")
        return ""
    out = []
    for m in reversed(rows):
        who = getattr(m.author, "display_name", "?")
        body = (m.content or "").strip()
        if not body:
            continue
        out.append(f"[{m.created_at.astimezone():%Y-%m-%d %H:%M}] {who}: {body}")
    return "\n".join(out)
