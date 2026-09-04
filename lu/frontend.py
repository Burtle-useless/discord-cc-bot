"""DiscordFrontend：實作 `protocol.Frontend`，一個頻道一個實例。

`emit(ev)` **永不拋例外、不阻塞**：事件先進 asyncio.Queue，由該頻道一個 render task
消化；任何一則事件畫不出來只記 log，下一則照畫。`ask(req)` 的語意跟 butler
`transport/hub.py` 的 `SseFrontend.ask`／`pending_asks`／`resolve` 一模一樣：
發 ask.request 事件（render task 畫成按鈕）、等 Future 或逾時回 None、
`resolve()` 由按鈕回呼填答案。

事件 → Discord 的對照表在 `worklog/2026-09-03 新架構規格.md` 第 5 節；跟規格不同的
兩處：
  1. `reply.final` 這裡**會送**回覆（規格寫不動作）：`turn.done` 的 markdown 在
     engine 端截到 200 字，整篇回覆只有 reply.final 帶得出來；續跑每輪一則，
     先到先畫，使用者不必等全部跑完。
  2. `[[ASK:]]` 的選項跟著 reply.final 的 `ask` 欄位來（inline ask，engine 不會為它
     發 ask.request）：畫成按鈕，按下去等於使用者送了一則新訊息（走 hooks.submit）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord

import outbox
from engine.errors import AUTO_RESUME_MAX_SEC, parse_resets_at
from protocol import AskRequest, AskResponse, Event, make_event

from . import files, voice
from .canvas import Canvas, RawStore
from .i18n import err_text, t
from .render import MAX_MSG, CanvasState, fence, pipe_tables_to_code, split_message

log = logging.getLogger(__name__)

# 任務跑超過這麼多秒，收工時 @ 使用者一下（手機會震，人可以先離開）
NOTIFY_AFTER_SEC = 60.0
# 危險確認的指令原文超過這個長度就先另外貼、再貼按鈕那則（單則 2000 字上限）
ASK_RAW_INLINE_MAX = 1500
# 超長回覆改存成 .md 檔附上（切成四則以上的那種），手機上好讀又能存檔
LONG_REPLY_FILE_AT = 3 * MAX_MSG

REACT_QUEUED = "⏳"
REACT_STEERED = "⚡"
REACT_TAKEN = "▶️"
REACT_DROPPED = "🚫"

_RENAMED_RE = re.compile(r"^conv_renamed:(.*)$", re.DOTALL)


@dataclass
class Hooks:
    """前端需要的外部能力，由 bootstrap 注入。測試塞假的。"""

    channel_of: Callable[[int], Any | None]                     # channel id → discord 頻道
    submit: Callable[[str, str, str], Awaitable[Any]]           # (conv_id, text, speaker) → Submitted
    on_renamed: Callable[[str, str], Awaitable[None]]           # (conv_id, title)
    user_ok: Callable[[int], bool]                              # 這個 user id 能按按鈕嗎
    raw_store: RawStore
    ctx_limit: Callable[[str], int]                             # conv_id → context 上限（0＝不知道）
    tmp_dir: Path                                               # 超長回覆存 .md 的地方


# DynamicItem 的回呼拿不到 frontend 實例，只能從模組層拿 hooks（bootstrap 裝一次）
_hooks: Hooks | None = None


def install_hooks(hooks: Hooks) -> None:
    global _hooks
    _hooks = hooks


@dataclass(slots=True)
class _Mark:
    """使用者訊息上的 reaction 狀態（⏳ 排隊中 → ▶️ 已讀走／🚫 已取消）。"""

    message: Any
    waiting: bool = False      # ⏳ 已經加上去了
    taken: bool = False        # taken 事件比 ⏳ 先到：那就不要再加 ⏳


# ── 持久化按鈕 ───────────────────────────────────────────────────────────────
async def _send_ephemeral_chunks(inter: discord.Interaction, text: str) -> None:
    chunks = split_message(text) or [text]
    await inter.response.send_message(chunks[0], ephemeral=True)
    for c in chunks[1:]:
        await inter.followup.send(c, ephemeral=True)


class RawDetailButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"lu:raw:(?P<key>[0-9a-f]{12})",
):
    """「詳細」：一段的指令原文超過遮罩上限時掛的按鈕，按了 ephemeral 回全文。

    persistent（timeout=None＋固定 custom_id），服務重啟後照樣按得動；原文在
    RawStore 落檔，找不到就說「已經不在了」。
    """

    def __init__(self, key: str, label: str | None = None) -> None:
        super().__init__(discord.ui.Button(
            label=label or t("btn_detail"), style=discord.ButtonStyle.secondary,
            custom_id=f"lu:raw:{key}",
        ))
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button,
                             match: re.Match[str], /) -> "RawDetailButton":
        return cls(match["key"])

    async def callback(self, interaction: discord.Interaction) -> None:
        raw = _hooks.raw_store.get(self.key) if _hooks is not None else None
        if not isinstance(raw, str):
            await interaction.response.send_message(t("detail_gone"), ephemeral=True)
            return
        await _send_ephemeral_chunks(interaction, t("detail_title") + "\n" + fence(raw))


class PickButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"lu:pick:(?P<key>[0-9a-f]{12})",
):
    """[[ASK:]] 的選項按鈕：按下去＝使用者送了一則新訊息（那個選項的文字）。

    同樣 persistent：協定要求的「選項跟著回覆走、不逾時、重開還在」在 Discord 上
    就是這個。RawStore 存 {conv, label}。
    """

    def __init__(self, key: str, label: str) -> None:
        super().__init__(discord.ui.Button(
            label=label[:80], style=discord.ButtonStyle.primary, custom_id=f"lu:pick:{key}",
        ))
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button,
                             match: re.Match[str], /) -> "PickButton":
        return cls(match["key"], str(getattr(item, "label", "") or "…"))

    async def callback(self, interaction: discord.Interaction) -> None:
        hooks = _hooks
        payload = hooks.raw_store.get(self.key) if hooks is not None else None
        if hooks is None or not isinstance(payload, dict):
            await interaction.response.send_message(t("pick_gone"), ephemeral=True)
            return
        if not hooks.user_ok(interaction.user.id):
            await interaction.response.send_message(t("ask_not_allowed"), ephemeral=True)
            return
        label = str(payload.get("label") or "")
        conv = str(payload.get("conv") or "")
        # 先把按鈕收掉再送：送出去那一刻 worker 就開始畫畫布，訊息順序才對
        view = self.view
        if view is not None:
            for it in view.children:
                it.disabled = True
        try:
            content = (interaction.message.content if interaction.message else "") or ""
            await interaction.response.edit_message(
                content=(content + "\n" + t("pick_submitted", label=label))[:MAX_MSG + 90],
                view=view,
            )
        except Exception:  # noqa: BLE001
            try:
                await interaction.response.defer()
            except Exception:  # noqa: BLE001
                pass
        await hooks.submit(conv, label, interaction.user.display_name)


def detail_view(keys: list[str]) -> discord.ui.View:
    """畫布上的「詳細」按鈕組。多段各一顆，超過 25 顆的丟掉（Discord 上限）。"""
    view = discord.ui.View(timeout=None)
    many = len(keys) > 1
    for i, k in enumerate(keys[:25], 1):
        view.add_item(RawDetailButton(k, f"{t('btn_detail')} {i}" if many else None))
    return view


def choice_view(store: RawStore, conv_id: str, choices: list[dict]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for c in choices[:25]:
        label = str(c.get("label") or c.get("id") or "")
        if not label:
            continue
        key = store.put({"conv": conv_id, "label": label})
        view.add_item(PickButton(key, label))
    return view


class AskView(discord.ui.View):
    """ask.request 的按鈕：答案交回 `DiscordFrontend.resolve`。逾時由 ask() 那邊管。"""

    def __init__(self, fe: "DiscordFrontend", ask_id: str, kind: str, choices: list[dict]) -> None:
        super().__init__(timeout=None)
        self.fe = fe
        self.ask_id = ask_id
        for c in choices[:25]:
            cid = str(c.get("id") or "")
            label = str(c.get("label") or cid)[:80]
            if kind == "confirm_destructive":
                style = discord.ButtonStyle.danger if cid == "yes" else discord.ButtonStyle.secondary
            else:
                style = discord.ButtonStyle.primary
            btn = discord.ui.Button(label=label, style=style, custom_id=f"lu:ask:{ask_id}:{cid}"[:100])
            btn.callback = self._make_cb(cid)
            self.add_item(btn)

    def _make_cb(self, choice_id: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def _cb(inter: discord.Interaction) -> None:
            if not self.fe.hooks.user_ok(inter.user.id):
                await inter.response.send_message(t("ask_not_allowed"), ephemeral=True)
                return
            if not self.fe.resolve(self.ask_id, choice_id):
                await inter.response.send_message(t("question_ended"), ephemeral=True)
                return
            try:
                await inter.response.defer()
            except Exception:  # noqa: BLE001
                pass
        return _cb


# ── 前端本體 ─────────────────────────────────────────────────────────────────
class DiscordFrontend:
    """protocol.Frontend 的 Discord 實作。engine 只看得到 emit／ask 兩個方法。"""

    def __init__(self, conv_id: str, channel_id: int, hooks: Hooks, *,
                 edit_interval: float = 2.0) -> None:
        self.conv_id = conv_id
        self.channel_id = channel_id
        self.hooks = hooks
        self.edit_interval = edit_interval
        self._q: asyncio.Queue[Event] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._cur_turn = ""
        self._pending: dict[str, tuple[AskRequest, asyncio.Future[AskResponse]]] = {}
        self._ask_msgs: dict[str, tuple[Any, AskView, str]] = {}   # ask_id → (訊息, view, body)
        self._canvas: Canvas | None = None
        self._done_canvas: Canvas | None = None      # 出錯後留著等 waiting 狀態畫倒數
        self._said = False
        self._resets_at: float | None = None
        self._bg_seen: dict[str, str] = {}
        self.marks: dict[str, _Mark] = {}
        self.last_author: Any | None = None          # 最後一位發言者：長任務收工時 @ 他
        self.turn_started_at = 0.0
        self.handled = 0                              # 統計：處理了幾則事件（測試用）

    # ── Frontend 介面 ──
    async def emit(self, ev: Event) -> None:
        """永不拋例外、不阻塞：只放進佇列，畫的事交給 render task。"""
        try:
            if ev.type == "turn.start":
                self._cur_turn = ev.turn_id
            self._q.put_nowait(ev)
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._loop())
        except Exception:  # noqa: BLE001
            pass

    async def ask(self, req: AskRequest) -> AskResponse | None:
        """提問並等答案。逾時回 None，呼叫端一律 fail-closed 當作拒絕。"""
        ask_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future[AskResponse] = asyncio.get_running_loop().create_future()
        self._pending[ask_id] = (req, fut)
        await self.emit(make_event(
            self.conv_id, self._cur_turn, "ask.request",
            ask_id=ask_id, kind=req.kind, title=req.title, body=req.body,
            raw=req.raw, require_biometric=req.require_biometric, timeout_sec=req.timeout_sec,
            choices=[{"id": c.id, "label": c.label, "detail": c.detail} for c in req.choices],
        ))
        answer: AskResponse | None = None
        try:
            answer = await asyncio.wait_for(fut, timeout=req.timeout_sec)
            return answer
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None
        finally:
            self._pending.pop(ask_id, None)
            await self.emit(make_event(
                self.conv_id, self._cur_turn, "ask.resolved",
                ask_id=ask_id, choice_id=answer.choice_id if answer is not None else "",
            ))

    def resolve(self, ask_id: str, choice_id: str) -> bool:
        """按鈕回呼把答案交回等待中的 ask。回傳是否成功對上。"""
        entry = self._pending.get(ask_id)
        if entry is None or entry[1].done():
            return False
        entry[1].set_result(AskResponse(choice_id=choice_id))
        return True

    def pending_asks(self) -> list[dict[str, Any]]:
        return [
            {"ask_id": aid, "kind": req.kind, "title": req.title, "body": req.body, "raw": req.raw,
             "choices": [{"id": c.id, "label": c.label, "detail": c.detail} for c in req.choices]}
            for aid, (req, fut) in self._pending.items() if not fut.done()
        ]

    async def close(self) -> None:
        """頻道沒了：停掉 render task。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except BaseException:  # noqa: BLE001
                pass
        self._task = None

    # ── 使用者訊息的 reaction ──
    async def mark_submitted(self, message: Any, msg_id: str, *, queued: bool, steered: bool) -> None:
        """on_message 送完 worker.submit 之後呼叫：插話加 ⚡、排隊加 ⏳。"""
        mark = self.marks.setdefault(msg_id, _Mark(message=message))
        if len(self.marks) > 200:
            for k in list(self.marks)[:-100]:
                self.marks.pop(k, None)
        try:
            if steered:
                await message.add_reaction(REACT_STEERED)
            elif queued and not mark.taken:
                await message.add_reaction(REACT_QUEUED)
                mark.waiting = True
        except Exception:  # noqa: BLE001
            pass

    async def _swap_mark(self, msg_id: str, to: str) -> None:
        mark = self.marks.pop(msg_id, None)
        if mark is None:
            # taken 比 on_message 的登記先到：留個記號讓它別再加 ⏳
            self.marks[msg_id] = _Mark(message=None, taken=True)
            return
        if not mark.waiting:
            return
        me = getattr(getattr(mark.message, "guild", None), "me", None)
        try:
            if me is not None:
                await mark.message.remove_reaction(REACT_QUEUED, me)
            await mark.message.add_reaction(to)
        except Exception:  # noqa: BLE001
            pass

    # ── render task ──
    async def _loop(self) -> None:
        while True:
            ev = await self._q.get()
            try:
                await self._handle(ev)
            except Exception:  # noqa: BLE001 — 一則畫不出來不能影響下一則
                log.exception("事件 %s 處理失敗（%s）", ev.type, self.conv_id)
            finally:
                self.handled += 1

    def _channel(self) -> Any | None:
        return self.hooks.channel_of(self.channel_id)

    def _new_canvas(self, channel: Any) -> Canvas:
        state = CanvasState(ctx_limit=lambda: self.hooks.ctx_limit(self.conv_id))
        return Canvas(
            channel, state, raw_store=self.hooks.raw_store,
            view_factory=detail_view, edit_interval=self.edit_interval,
        )

    async def _canvas_feed(self, ev: Event) -> None:
        if self._canvas is None:
            ch = self._channel()
            if ch is None:
                return
            self._canvas = self._new_canvas(ch)
            self.turn_started_at = time.monotonic()
        await self._canvas.feed(ev.type, ev.data)

    async def _handle(self, ev: Event) -> None:
        d = ev.data
        typ = ev.type
        if typ == "turn.start":
            self._done_canvas = None
            await self._canvas_feed(ev)
        elif typ in ("thinking.delta", "text.delta", "tool.call", "step.commit", "turn.end"):
            await self._canvas_feed(ev)
        elif typ == "status":
            await self._on_status(ev)
        elif typ == "reply.final":
            await self._on_reply(d)
        elif typ == "turn.done":
            await self._on_done(d)
        elif typ == "error":
            await self._on_error(d)
        elif typ == "ask.request":
            await self._on_ask_request(d)
        elif typ == "ask.resolved":
            await self._on_ask_resolved(d)
        elif typ == "file.offer":
            await self._on_file_offer(d)
        elif typ == "bg.state":
            await self._on_bg(d)
        elif typ == "message.taken":
            for mid in d.get("msg_ids") or []:
                await self._swap_mark(str(mid), REACT_TAKEN)
        elif typ == "message.dropped":
            for mid in d.get("msg_ids") or []:
                await self._swap_mark(str(mid), REACT_DROPPED)
        # user.message／notify／stream.reset／seq.gap／device.request／agenda.changed／
        # kanban.changed：Discord 用不到，忽略

    async def _on_status(self, ev: Event) -> None:
        note = str(ev.data.get("note") or "")
        m = _RENAMED_RE.match(note)
        if m:
            title = m.group(1).strip()
            if title:
                await self.hooks.on_renamed(self.conv_id, title)
            return
        if ev.data.get("phase") == "waiting":
            # 限流等待：這則多半在 error 之後、沒有進行中的畫布。倒數畫在剛才那張畫布上
            data = dict(ev.data)
            if not data.get("resets_at"):
                ts = self._resets_at or parse_resets_at(note)
                if ts:
                    data["resets_at"] = ts
            cv = self._canvas or self._done_canvas
            if cv is None:
                ch = self._channel()
                if ch is None:
                    return
                cv = self._done_canvas = self._new_canvas(ch)
            cv.closed = False
            cv.state.feed("status", data)
            await cv.paint_now()
            return
        await self._canvas_feed(ev)

    async def _speak(self, ch: Any, md: str) -> None:
        """開車模式時把回覆唸出來，附在文字後面。合成失敗就當沒這回事——
        語音是加值，不能因為它壞掉就讓人收不到回覆。"""
        if not voice.is_on():
            return
        try:
            wav, _text = await voice.speak(md)
            if wav is not None:
                await ch.send(file=discord.File(str(wav)))
                wav.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            log.exception("語音回覆失敗")

    async def _on_reply(self, d: dict) -> None:
        ch = self._channel()
        if ch is None:
            return
        if self._canvas is not None:
            self._canvas.state.feed("reply.final", d)
        md = str(d.get("markdown") or "").strip()
        ask = d.get("ask")
        if md:
            # 朗讀標記是給合成器看的，畫面上不該出現
            await send_long(ch, pipe_tables_to_code(voice.strip_speak_marks(md)),
                            tmp_dir=self.hooks.tmp_dir)
            self._said = True
            await self._speak(ch, md)
        if isinstance(ask, dict) and ask.get("choices"):
            title = str(ask.get("title") or "")
            body = t("ask_body", title=title) + "\n" + t("ask_choices_hint") if not md else t("ask_choices_hint")
            try:
                await ch.send(body, view=choice_view(self.hooks.raw_store, self.conv_id, list(ask["choices"])))
            except Exception:  # noqa: BLE001
                log.exception("選項按鈕送不出去")

    async def _on_done(self, d: dict) -> None:
        cv = self._canvas
        self._canvas = None
        if cv is not None:
            await cv.finalize(keep=cv.state.has_trace())
        elapsed = float(d.get("elapsed_ms") or 0) / 1000.0
        author = self.last_author
        if elapsed >= NOTIFY_AFTER_SEC and author is not None:
            ch = self._channel()
            if ch is not None:
                tip = t("notify_need_answer") if d.get("pending_ask") else t("notify_done")
                try:
                    await ch.send(f"{author.mention} ✅ {tip} · {elapsed:.0f}s")
                except Exception:  # noqa: BLE001
                    pass
        self._said = False

    async def _on_error(self, d: dict) -> None:
        kind = str(d.get("kind") or "UNKNOWN")
        ch = self._channel()
        cv = self._canvas
        self._canvas = None
        if kind == "STOPPED":
            partial = cv.state.stopped_partial() if cv is not None else ""
            if cv is not None:
                await cv.finalize(keep=cv.state.has_trace())
            if ch is None:
                return
            if partial:
                await send_long(ch, pipe_tables_to_code(partial) + "\n" + t("stopped_tail"),
                                tmp_dir=self.hooks.tmp_dir)
            else:
                try:
                    await ch.send(t("err_stopped"))
                except Exception:  # noqa: BLE001
                    pass
            self._said = False
            return
        resets = d.get("resets_at")
        self._resets_at = float(resets) if resets else None
        if kind == "RATE_LIMIT" and resets:
            wait = float(resets) - time.time()
            key = "err_rate_limit_auto" if 0 < wait <= AUTO_RESUME_MAX_SEC else "err_rate_limit_at"
            text = t(key, ts=int(float(resets)))
        else:
            text = err_text(kind)
        raw = str(d.get("raw") or "").strip()
        if raw and kind != "RATE_LIMIT":
            text += t("err_raw_block", kind=kind, raw=raw[:600].replace("```", "'''"))
        if cv is not None:
            await cv.show_error(text)
            self._done_canvas = cv
        elif ch is not None:
            try:
                await ch.send(text[:MAX_MSG + 90])
            except Exception:  # noqa: BLE001
                pass
        self._said = False

    async def _on_ask_request(self, d: dict) -> None:
        ch = self._channel()
        if ch is None:
            return
        ask_id = str(d.get("ask_id") or "")
        kind = str(d.get("kind") or "choose")
        title = str(d.get("title") or "")
        body = str(d.get("body") or "")
        raw = str(d.get("raw") or "")
        choices = list(d.get("choices") or [])
        if kind == "confirm_destructive":
            # 指令原文**完整列**，不摘要不截尾：說明講 A、指令做 B 正是攻擊面
            if raw and len(raw) > ASK_RAW_INLINE_MAX:
                for c in split_message(fence(raw)):
                    await ch.send(c)
                text = t("ask_confirm_body_raw_above", body=f"**{title}**\n{body}".strip())
            else:
                text = t("ask_confirm_body", raw=raw.replace("```", "'''"), body=f"**{title}**\n{body}".strip())
        else:
            text = t("ask_body", title=title)
            if body:
                text += "\n" + body
            text += "\n" + t("ask_choices_hint")
        view = AskView(self, ask_id, kind, choices)
        try:
            msg = await ch.send(text[:MAX_MSG + 90], view=view)
        except Exception:  # noqa: BLE001
            log.exception("提問送不出去")
            return
        self._ask_msgs[ask_id] = (msg, view, text)

    async def _on_ask_resolved(self, d: dict) -> None:
        ask_id = str(d.get("ask_id") or "")
        entry = self._ask_msgs.pop(ask_id, None)
        if entry is None:
            return
        msg, view, text = entry
        choice_id = str(d.get("choice_id") or "")
        label = choice_id
        for it in view.children:
            it.disabled = True
            cid = str(getattr(it, "custom_id", "") or "")
            if choice_id and cid.endswith(f":{choice_id}"):
                label = str(getattr(it, "label", "") or choice_id)
        view.stop()
        tail = t("ask_answered", label=label) if choice_id else t("ask_timed_out")
        try:
            await msg.edit(content=(text + "\n" + tail)[:MAX_MSG + 90], view=view)
        except Exception:  # noqa: BLE001
            pass

    async def _on_file_offer(self, d: dict) -> None:
        ch = self._channel()
        if ch is None:
            return
        item = await asyncio.to_thread(outbox.get, str(d.get("file_id") or ""))
        if not item:
            return
        await files.send_file(ch, item["path"], str(d.get("note") or item.get("note") or ""))

    async def _on_bg(self, d: dict) -> None:
        tasks = list(d.get("tasks") or [])
        if self._canvas is not None:
            await self._canvas.feed("bg.state", d)
        else:
            # 回合外的變化：剛結束的工作講一句
            ch = self._channel()
            for task in tasks:
                tid = str(task.get("id") or "")
                st = str(task.get("status") or "")
                prev = self._bg_seen.get(tid)
                if st != "running" and prev == "running" and ch is not None:
                    key = {"failed": "bg_failed_line", "stopped": "bg_stopped_line"}.get(st, "bg_done_line")
                    try:
                        await ch.send(t(key, desc=str(task.get("desc") or "")[:80]))
                    except Exception:  # noqa: BLE001
                        pass
        self._bg_seen = {str(x.get("id") or ""): str(x.get("status") or "") for x in tasks}


# ── 分段送訊息 ───────────────────────────────────────────────────────────────
async def send_long(channel: Any, text: str, *, tmp_dir: Path | None = None) -> None:
    """把回覆切段送出，一個字都不丟；超長改存 .md 附上。中途一則失敗不丟後面的（B15）。"""
    text = (text or "").strip()
    if not text:
        return
    if len(text) > LONG_REPLY_FILE_AT and tmp_dir is not None:
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            fp = tmp_dir / f"lu_reply_{uuid.uuid4().hex[:8]}.md"
            fp.write_text(text, encoding="utf-8")
            try:
                preview = text[:1500].rstrip()
                await channel.send(preview + t("reply_long_preview"), file=discord.File(str(fp)))
                return
            finally:
                fp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — 附件那條路失敗就退回切段
            log.exception("超長回覆存檔失敗，改切段")
    chunks = split_message(text)
    for i, c in enumerate(chunks):
        try:
            await channel.send(c)
        except Exception:  # noqa: BLE001
            log.warning("回覆第 %d/%d 段送不出去", i + 1, len(chunks))
        if i + 1 < len(chunks):
            await asyncio.sleep(0.3)
