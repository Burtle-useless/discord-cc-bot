"""/sessions：接管電腦上既有的 Claude Code session（複印成新頻道）。

走 engine.sessions：`scan_sessions` 列出還沒被接管的、`fork_session` 複印成獨立的新
session（**一定要複印**：兩邊 --resume 同一個 id 會把逐字稿寫成分叉，見 engine.sessions
的說明）。接管後標題先用開場白前 60 字頂著，背景再請 Haiku 取一個短標題
（engine.titles.rename_adopted），頻道名跟著改。

分頁：一頁 25 筆（select menu 上限），◀ ▶ 翻頁，狀態綁在 view 上。

TODO（下一批）：
  - /search：語意搜尋（舊 _semantic_search，fastembed 選配）→ 找到就走同一條接管路
  - /recall：原話帳本核對（舊 user_messages.jsonl 那套）
  - /handoff：交接稿
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from engine import sessions as sess_mod
from engine import titles

from ..i18n import t
from ..profile import conv_id as conv_of

if TYPE_CHECKING:
    from ..bootstrap import BotContext

log = logging.getLogger(__name__)
PAGE = 25


class SessionsView(discord.ui.View):
    def __init__(self, ctx: "BotContext", user_id: int, q: str) -> None:
        super().__init__(timeout=600)
        self.ctx = ctx
        self.user_id = user_id
        self.q = q
        self.page = 0
        self.entries: list[dict] = []
        # 搜尋結果是一次算完的一整份，沒有下一頁可翻（見 set_entries）
        self.searched = False

    async def load(self) -> None:
        self.entries = await asyncio.to_thread(
            sess_mod.scan_sessions, PAGE, self.q, self.page * PAGE,
        )
        self._rebuild()

    def set_entries(self, entries: list[dict]) -> None:
        """直接餵一份結果（`/search` 用：搜尋自己排序，不走 scan_sessions 的分頁）。

        餵進來之後翻頁鈕會失效——搜尋結果是一次算完的一整份，沒有「下一頁」可翻。
        """
        self.entries = entries[:PAGE]
        self.searched = True
        self._rebuild()

    def text(self) -> str:
        if not self.entries:
            return t("sessions_none")
        if self.searched:
            return ""      # 搜尋自己有一行說明（見 commands/memory.py 的 search_head）
        return t("sessions_header", page=self.page + 1)

    def _rebuild(self) -> None:
        self.clear_items()
        if self.entries:
            options = []
            for i, e in enumerate(self.entries):
                dt = datetime.fromtimestamp(e["mtime"]).strftime("%m/%d %H:%M")
                mark = "🤖 " if e.get("sidechain") else ""
                options.append(discord.SelectOption(
                    label=(mark + (e["title"] or t("untitled")))[:100],
                    description=f"{dt} · {e['cwd']}"[:100], value=str(i),
                ))
            sel = discord.ui.Select(placeholder=t("sessions_pick"), options=options)
            sel.callback = self._on_pick
            self.add_item(sel)
        prev_btn = discord.ui.Button(label=t("btn_prev"), style=discord.ButtonStyle.secondary,
                                     disabled=self.searched or self.page == 0)
        prev_btn.callback = self._on_prev
        next_btn = discord.ui.Button(label=t("btn_next"), style=discord.ButtonStyle.secondary,
                                     disabled=self.searched or len(self.entries) < PAGE)
        next_btn.callback = self._on_next
        self.add_item(prev_btn)
        self.add_item(next_btn)

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        return inter.user.id == self.user_id

    async def _on_prev(self, inter: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        await self.load()
        await inter.response.edit_message(content=self.text(), view=self)

    async def _on_next(self, inter: discord.Interaction) -> None:
        self.page += 1
        await self.load()
        await inter.response.edit_message(content=self.text(), view=self)

    async def _on_pick(self, inter: discord.Interaction) -> None:
        sel = (inter.data.get("values") if inter.data else None) or []
        if not sel or inter.guild is None:
            await inter.response.edit_message(content=t("adopt_no_sidebar"), view=None)
            return
        entry = self.entries[int(sel[0])]
        sid = entry["session_id"]
        await inter.response.defer()
        new_id = await asyncio.to_thread(sess_mod.fork_session, sid)
        if not new_id:
            await inter.edit_original_response(content=t("adopt_failed"), view=None)
            return
        cwd = entry.get("cwd") or await asyncio.to_thread(sess_mod.session_cwd, sid)
        title = (entry.get("title") or t("untitled"))[:60]
        ch = await self.ctx.sidebar.open_channel(
            inter.guild, session_id=new_id, forked_from=sid, title=title, cwd=cwd,
        )
        if ch is None:
            await inter.edit_original_response(content=t("adopt_no_sidebar"), view=None)
            return
        conv = conv_of(ch.id)
        # 背景請 Haiku 取短標題；apply_title 發 conv_renamed，frontend 收到就改頻道名
        self.ctx.keep_task(asyncio.create_task(
            titles.rename_adopted(conv, self.ctx.frontend_for(conv)),
        ))
        await inter.edit_original_response(content=t("adopted_to_channel", mention=ch.mention), view=None)


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth

    @tree.command(name="sessions", description=t("cmd_sessions_desc"))
    @app_commands.describe(q=t("sessions_q_desc"))
    async def cmd_sessions(inter: discord.Interaction, q: str = "") -> None:
        if not await auth.check(inter):
            return
        await inter.response.defer(ephemeral=True)
        view = SessionsView(ctx, inter.user.id, q.strip())
        try:
            await view.load()
        except Exception:  # noqa: BLE001
            log.exception("掃 session 失敗")
            await inter.followup.send(t("sessions_none"), ephemeral=True)
            return
        await inter.followup.send(view.text(), view=view if view.entries else None, ephemeral=True)

    # TODO（下一批）：/search、/recall、/handoff — 見模組說明
