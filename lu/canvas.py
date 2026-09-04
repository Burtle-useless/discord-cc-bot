"""畫布訊息：建立、節流編輯、超長翻頁，加上「詳細」按鈕背後的原文倉庫。

這裡不 import discord：頻道與訊息物件都是鴨子型別（`channel.send()`、`msg.edit()`、
`msg.delete()`），「詳細」按鈕的 View 由呼叫端用 `view_factory(keys)` 造，
所以 tests/test_frontend.py 用假物件就能驗節流與翻頁。

節流規則（規格 B10）：每 `edit_interval` 秒最多編輯一次，**而且內容變了才編輯**；
閒置沒有事件就不編輯。做法是事件進來只標記髒、排一個延遲刷新，時間到再畫一次——
最後一則事件之後仍會補畫，畫面不會停在倒數第二個狀態。

翻頁規則（規格 B13）：整則超過 1900 字時把軌跡凍結進目前這則（**不截尾**，超長就
切成幾則），另開一則只帶狀態列的新畫布繼續。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .i18n import t
from .render import MAX_MSG, CanvasState, split_message

log = logging.getLogger(__name__)


class RawStore:
    """「詳細」按鈕與選項按鈕背後的小倉庫：key → 任意 JSON 值，落檔、有上限。

    落檔是因為按鈕是 persistent view：服務重啟後使用者按下去，還要拿得到那段原文。
    只留最近 [keep] 筆，舊的按下去會看到「已經不在了」，不會炸。
    """

    def __init__(self, path: Path | None, keep: int = 400) -> None:
        self._path = path
        self._keep = keep
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data = data
        except Exception:  # noqa: BLE001 — 壞了就從空的開始，這份只是快取
            log.warning("讀 %s 失敗，原文倉庫從空的開始", self._path.name)

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:  # noqa: BLE001
            log.exception("寫 %s 失敗", self._path.name)

    def put(self, value: Any) -> str:
        key = uuid.uuid4().hex[:12]
        self._data[key] = value
        while len(self._data) > self._keep:
            del self._data[next(iter(self._data))]      # dict 保序，最舊的在最前面
        self._save()
        return key

    def get(self, key: str) -> Any | None:
        return self._data.get(key)


class Canvas:
    """一則會被反覆編輯的 Discord 訊息，內容由 `render.CanvasState` 決定。"""

    def __init__(
        self,
        channel: Any,
        state: CanvasState,
        *,
        raw_store: RawStore,
        view_factory: Callable[[list[str]], Any] | None = None,
        edit_interval: float = 2.0,
    ) -> None:
        self.channel = channel
        self.state = state
        self.raw_store = raw_store
        self.view_factory = view_factory
        self.edit_interval = edit_interval
        self.msg: Any | None = None
        self.keys: list[str] = []            # 目前這則畫布掛的「詳細」原文 key
        self._painted_keys: list[str] = []
        self._last_content = ""
        self._last_edit = float("-inf")
        self._flush_task: asyncio.Task | None = None
        self.closed = False
        self.edits = 0                        # 統計：實際 edit／send 了幾次（測試用）

    # ── 餵事件 ──
    async def feed(self, type_: str, data: dict) -> None:
        self.state.feed(type_, data)
        for long in self.state.take_long_raws():
            self.keys.append(self.raw_store.put(long))
        self._schedule()

    def _schedule(self) -> None:
        if self.closed:
            return
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        wait = self.edit_interval - (time.monotonic() - self._last_edit)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            await self.paint()
        except Exception:  # noqa: BLE001 — 畫布畫不出來不能拖垮渲染迴圈
            log.exception("畫布刷新失敗")

    # ── 畫 ──
    async def paint(self) -> None:
        """把目前狀態畫出去：內容沒變就不動 Discord；超過上限先翻頁。"""
        if self.closed:
            return
        content = self.state.render()
        if not content:
            return
        if len(content) > MAX_MSG:
            await self._roll()
            content = self.state.render()
        await self._put(content)

    def _view_kw(self, force: bool = False) -> dict[str, Any]:
        """要不要在這次 send／edit 帶 view：key 清單變了才帶（帶 None 會把既有按鈕拆掉）。"""
        if self.view_factory is None:
            return {}
        if self.keys and (force or self.keys != self._painted_keys):
            return {"view": self.view_factory(list(self.keys))}
        return {}

    async def _put(self, content: str) -> None:
        content = content[:MAX_MSG + 90]      # 最後防線；正常情況 _roll 已經處理掉超長
        kw = self._view_kw()
        try:
            if self.msg is None:
                self.msg = await self.channel.send(content, **kw)
                self.edits += 1
            elif content != self._last_content or kw:
                await self.msg.edit(content=content, **kw)
                self.edits += 1
            else:
                return
        except Exception as e:  # noqa: BLE001
            # 404＝畫布被人刪了：下次重建一則，不要對著不存在的訊息一直 edit
            if getattr(e, "status", None) == 404:
                self.msg = None
                self._last_content = ""
                return
            log.warning("畫布 edit 失敗：%r", e)
            return
        self._last_content = content
        self._painted_keys = list(self.keys)
        self._last_edit = time.monotonic()

    async def _roll(self) -> None:
        """軌跡撞上限：凍結成純軌跡（不截尾、超長切多則），另開一則畫布。"""
        self.state.close_segment()
        for long in self.state.take_long_raws():
            self.keys.append(self.raw_store.put(long))
        frozen = self.state.trace_text()
        chunks = split_message(frozen) or [""]
        kw = self._view_kw()
        try:
            if self.msg is None:
                if chunks[0]:
                    await self.channel.send(chunks[0], **kw)
            else:
                await self.msg.edit(content=chunks[0] or "…", **kw)
            for c in chunks[1:]:
                await self.channel.send(c)
        except Exception as e:  # noqa: BLE001
            log.warning("畫布翻頁失敗：%r", e)
        self.edits += 1
        # 新畫布：軌跡清空、頂部改成「接續」、按鈕 key 歸零
        self.state.lines.clear()
        self.state.header = t("canvas_continued")
        self.keys = []
        self._painted_keys = []
        self.msg = None
        self._last_content = ""

    async def _cancel_flush(self) -> None:
        task = self._flush_task
        self._flush_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001
                pass

    async def finalize(self, *, keep: bool) -> None:
        """收工：keep＝凍結軌跡（去掉狀態列）；否則刪掉畫布。之後這個物件不再畫。"""
        self.closed = True
        await self._cancel_flush()
        if not keep:
            if self.msg is not None:
                try:
                    await self.msg.delete()
                except Exception:  # noqa: BLE001
                    pass
                self.msg = None
            return
        self.state.close_segment()
        for long in self.state.take_long_raws():
            self.keys.append(self.raw_store.put(long))
        self.state.status_cleared = True
        self.state.error_text = ""
        content = self.state.trace_text()
        if not content:
            if self.msg is not None:
                try:
                    await self.msg.delete()
                except Exception:  # noqa: BLE001
                    pass
            return
        chunks = split_message(content)
        kw = self._view_kw()
        try:
            if self.msg is None:
                await self.channel.send(chunks[0], **kw)
            elif chunks[0] != self._last_content or kw:
                await self.msg.edit(content=chunks[0], **kw)
            for c in chunks[1:]:
                await self.channel.send(c)
            self.edits += 1
        except Exception as e:  # noqa: BLE001
            log.warning("畫布收尾失敗：%r", e)

    async def show_error(self, text: str) -> None:
        """錯誤取代狀態列，軌跡留著；沒有畫布就另發一則。之後這個物件不再自動畫。"""
        await self._cancel_flush()
        self.state.close_segment()
        for long in self.state.take_long_raws():
            self.keys.append(self.raw_store.put(long))
        self.state.error_text = text
        content = self.state.render()
        if len(content) > MAX_MSG:
            await self._roll()
            content = self.state.render()
        await self._put(content)

    async def paint_now(self) -> None:
        """不等節流立刻畫一次（限流等待的倒數那種「畫完就沒下一則事件」的情況）。"""
        await self._cancel_flush()
        await self.paint()
