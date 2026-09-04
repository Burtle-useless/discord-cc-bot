"""側欄：頻道＝對話。分類、入口頻道、自動命名同步頻道名、超過 20 個刪最舊、頻道刪除清理。

沿用舊 discord_bot.py 的做法（_ensure_sidebar／_promote_entry_channel／_open_sidebar_channel／
_bump_channel_to_top／_prune_old_convos），差別只在狀態不再自己養：對話狀態在
`engine.state`（`dc:<channel_id>`）、標題在 `engine.state` 的 titles、進行中與否問 worker。

Discord 的硬限制要記得：單一分類 50 個頻道（撞到後建頻道直接 400），頻道改名
10 分鐘 2 次（超過會被 rate limit 卡住很久，所以改名失敗一律吞掉不重試）。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import discord

from engine import client_pool
from engine import state as state_mod
from engine.state import get_state

from .auth import Auth
from .i18n import t
from .profile import conv_id as conv_of

log = logging.getLogger(__name__)

# 側欄分類內對話頻道數上限（軟上限，遠低於 Discord 的 50 硬上限）
MAX_CONVOS = 20
# 入口頻道名的前綴：轉正過程若剛好卡在重啟，可能短暫存在多個，靠它認
ENTRY_MARK = "➕"


def safe_channel_name(title: str) -> str:
    """標題 → 可用的頻道名：去頭尾空白、換行轉空白、截到 90 字。"""
    name = " ".join((title or "").split())[:90].strip()
    return name or t("untitled_chat")


class Sidebar:
    """管一個 guild 的側欄分類。"""

    def __init__(
        self,
        client: discord.Client,
        auth: Auth,
        worker: Any,
        frontends: dict[str, Any],
        *,
        category_name: str | None = None,
        entry_name: str | None = None,
        default_cwd: Path | None = None,
    ) -> None:
        self.client = client
        self.auth = auth
        self.worker = worker
        self.frontends = frontends
        self.category_name = category_name or t("sidebar_category_default")
        self.entry_name = entry_name or t("sidebar_entry_default")
        self.default_cwd = default_cwd or Path.home()
        self.category_id: int | None = None
        self.entry_id: int | None = None
        self._promoting = False

    # ── 查詢 ──
    def is_entry(self, channel_id: int) -> bool:
        return self.entry_id is not None and channel_id == self.entry_id

    def _category(self) -> discord.CategoryChannel | None:
        ch = self.client.get_channel(self.category_id) if self.category_id else None
        return ch if isinstance(ch, discord.CategoryChannel) else None

    def _entry(self) -> discord.TextChannel | None:
        ch = self.client.get_channel(self.entry_id) if self.entry_id else None
        return ch if isinstance(ch, discord.TextChannel) else None

    def _convos(self, category: discord.CategoryChannel) -> list[discord.TextChannel]:
        return [c for c in category.text_channels
                if c.id != self.entry_id and not c.name.startswith(ENTRY_MARK)]

    # ── 開機／重連 ──
    async def ensure(self, guild: discord.Guild) -> None:
        """確保分類與入口頻道存在、把分類底下的對話頻道納入白名單。重連也要跑（B17）。"""
        try:
            category = discord.utils.get(guild.categories, name=self.category_name)
            if category is None:
                category = await guild.create_category(self.category_name)
            self.category_id = category.id
            entry_candidates: list[discord.TextChannel] = []
            for ch in category.text_channels:
                if ch.name == self.entry_name or ch.name.startswith(ENTRY_MARK):
                    entry_candidates.append(ch)
                else:
                    self.auth.add_channel(ch.id, persist=False)
                    self._ensure_titled(ch)
            entry: discord.TextChannel | None = None
            if entry_candidates:
                entry_candidates.sort(key=lambda c: c.position)
                entry = entry_candidates[0]
                for extra in entry_candidates[1:]:      # 其餘 ➕ 頻道收編成對話頻道
                    self.auth.add_channel(extra.id, persist=False)
                    self._ensure_titled(extra)
            # 使用者可能把對話頻道搬去別的分類：靠 session 對應把仍存在的補回白名單
            for conv in state_mod.list_conversations():
                cid = _channel_id_of(conv["conv_id"])
                if cid is not None and isinstance(guild.get_channel(cid), discord.TextChannel):
                    self.auth.add_channel(cid, persist=False)
            self.auth.save_channels()
            if entry is None:
                entry = await guild.create_text_channel(self.entry_name, category=category, position=0)
            self.entry_id = entry.id
            self.auth.entry_channel_id = entry.id
            if category.text_channels and category.text_channels[0].id != entry.id:
                try:
                    await entry.move(beginning=True, category=category)
                except Exception as e:  # noqa: BLE001
                    log.warning("入口置頂失敗：%r", e)
            await self._ensure_entry_prompt(entry)
            log.info("側欄就緒 category=%s entry=%s", self.category_id, self.entry_id)
        except discord.Forbidden:
            log.error("缺少管理頻道權限，略過側欄初始化")
        except Exception:  # noqa: BLE001
            log.exception("側欄初始化失敗")

    def _ensure_titled(self, ch: discord.TextChannel) -> None:
        """既有頻道已經有名字：把它當標題記下來，autoname 才不會在重啟後又改一次名。"""
        conv = conv_of(ch.id)
        if state_mod.get_title(conv) is None:
            state_mod.set_title(conv, ch.name)

    async def _ensure_entry_prompt(self, entry: discord.TextChannel) -> None:
        """入口頻道發一則純文字提示（避免重複洗頻）；順手清掉舊版遺留的按鈕訊息。"""
        try:
            has_prompt = False
            me = self.client.user
            async for m in entry.history(limit=20):
                if me is None or m.author.id != me.id:
                    continue
                if m.components:
                    try:
                        await m.delete()
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                has_prompt = True
            if not has_prompt:
                await entry.send(t("entry_message"))
        except Exception as e:  # noqa: BLE001
            log.warning("入口提示訊息處理失敗：%r", e)

    # ── 開新對話 ──
    async def promote_entry(self, old_entry: discord.TextChannel) -> bool:
        """『原地開新對話』：入口頻道就地轉成對話頻道，另建一個新入口頂到最上面。

        回 True＝已轉正（呼叫端把這句話當這通新對話的第一句照常處理）；False＝沒轉成
        （並發重入／缺分類／缺權限），呼叫端要明講、不要靜默吞掉那則訊息。
        """
        if self._promoting:
            return False
        guild = old_entry.guild
        category = old_entry.category
        if guild is None or category is None or category.id != self.category_id:
            return False
        self._promoting = True
        try:
            await self.prune(category)
            new_entry = await guild.create_text_channel(self.entry_name, category=category)
            await new_entry.move(beginning=True, category=category)
            try:
                await new_entry.send(t("entry_message"))
            except Exception as e:  # noqa: BLE001
                log.warning("入口提示訊息發送失敗：%r", e)
            self.auth.add_channel(old_entry.id)
            try:
                await old_entry.edit(name=t("new_chat_channel"))
            except Exception as e:  # noqa: BLE001
                log.warning("入口轉正改名失敗：%r", e)
            self.entry_id = new_entry.id
            self.auth.entry_channel_id = new_entry.id
            await self.bump_to_top(old_entry)
            return True
        except discord.Forbidden:
            log.error("缺『管理頻道』權限，入口轉正失敗")
            return False
        except Exception:  # noqa: BLE001
            log.exception("入口轉正失敗")
            return False
        finally:
            self._promoting = False

    async def open_channel(
        self, guild: discord.Guild, *, session_id: str | None, forked_from: str | None,
        title: str, cwd: str | None,
    ) -> discord.TextChannel | None:
        """在側欄分類頂端建一個頻道並綁一個 session（接管用）。缺權限或失敗回 None。"""
        category = self._category()
        if category is None:
            return None
        await self.prune(category)
        try:
            ch = await guild.create_text_channel(safe_channel_name(title), category=category, position=1)
        except Exception:  # noqa: BLE001
            log.exception("建頻道失敗")
            return None
        conv = conv_of(ch.id)
        st = get_state(conv)
        st.session_id = session_id
        st.forked_from = forked_from
        st.cwd = Path(cwd) if cwd and Path(cwd).is_dir() else self.default_cwd
        st.ctx_tokens = 0
        state_mod.persist(st)
        state_mod.set_title(conv, title)
        self.auth.add_channel(ch.id)
        await self.bump_to_top(ch)
        return ch

    async def bump_to_top(self, channel: discord.TextChannel) -> None:
        """把側欄頻道移到入口下方（最新活動置頂）；已在頂端就不動，省 rate limit。"""
        try:
            category = channel.category
            if category is None or category.id != self.category_id:
                return
            entry = self._entry()
            if entry is None:
                return
            convo = self._convos(category)
            if convo and convo[0].id == channel.id:
                return
            await channel.move(after=entry, category=category)
        except Exception as e:  # noqa: BLE001
            log.warning("置頂失敗：%r", e)

    async def prune(self, category: discord.CategoryChannel, room_for: int = 1) -> None:
        """開新對話前騰空位：把分類內對話頻道刪到 MAX_CONVOS - room_for 個以下。直接刪、不通知。

        刪最下面的（越下面越久沒活動）；正在跑回合的跳過。刪除交給
        on_guild_channel_delete 善後。
        """
        try:
            convos = self._convos(category)
            over = len(convos) - (MAX_CONVOS - room_for)
            if over <= 0:
                return
            for ch in reversed(convos):
                if over <= 0:
                    break
                if self.worker.is_running(conv_of(ch.id)):
                    continue
                try:
                    await ch.delete(reason=f"對話頻道上限 {MAX_CONVOS}，清理最久未活動的")
                except Exception as e:  # noqa: BLE001
                    log.warning("清理舊頻道失敗（%s）：%r", ch.name, e)
                    continue
                log.info("已清理舊對話頻道：%s", ch.name)
                over -= 1
        except Exception:  # noqa: BLE001
            log.exception("清理舊頻道失敗")

    # ── 命名 ──
    async def rename(self, channel_id: int, title: str) -> None:
        """標題變了 → 改頻道名。Discord 限 10 分鐘 2 次，失敗吞掉（標題本身已經存好了）。"""
        ch = self.client.get_channel(channel_id)
        if not isinstance(ch, discord.TextChannel):
            return
        new = safe_channel_name(title)
        if ch.name == new:
            return
        try:
            await asyncio.wait_for(ch.edit(name=new), timeout=15)
        except Exception as e:  # noqa: BLE001
            log.warning("改頻道名失敗（%s → %s）：%r", ch.name, new, e)

    # ── 事件 ──
    async def on_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """對話頻道被刪：停 worker、關 client、清狀態與標題、移出白名單。逐字稿留在硬碟。"""
        if self.is_entry(channel.id):
            return
        conv = conv_of(channel.id)
        self.auth.remove_channel(channel.id)
        try:
            await self.worker.remove(conv)
        except Exception:  # noqa: BLE001
            log.exception("停 worker 失敗 %s", conv)
        fe = self.frontends.pop(conv, None)
        if fe is not None:
            await fe.close()
        await client_pool.drop(conv)
        state_mod.delete_conversation(conv)

    async def on_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """手動在側欄分類建頻道 → 當成一個新對話（由使用者自己命名，不自動改名）。"""
        if not isinstance(channel, discord.TextChannel):
            return
        if (channel.category_id != self.category_id or self.is_entry(channel.id)
                or channel.name.startswith(ENTRY_MARK) or self.auth.channel_ok(channel.id)):
            return
        self.auth.add_channel(channel.id)
        self._ensure_titled(channel)


def _channel_id_of(conv: str) -> int | None:
    from .profile import channel_id
    return channel_id(conv)
