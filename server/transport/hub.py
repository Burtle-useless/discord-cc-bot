"""事件匯流排與 SSE 前端實作。

續傳是這一層存在的理由。手機背景化必然斷線——這不是意外而是 Android 的常態，
所以架構上直接假設「背景＝斷線」，靠 ring buffer 把漏掉的補回來。
`Last-Event-ID` 是 SSE 協定內建的續傳錨點，OkHttp 的 okhttp-sse 直接支援。
"""
from __future__ import annotations

import asyncio
import uuid
from collections import deque
from typing import Any, AsyncIterator

import config
from protocol import AskRequest, AskResponse, Event, make_event
from protocol.events import _seq


class EventHub:
    """全域事件匯流排：一份 ring buffer + 多個訂閱者。

    一個裝置一條 SSE 連線（不是一個對話一條）——事件信封帶 conv_id 分流，
    一條連線就能承載全部對話，也繞開 HTTP/1.1 每網域 6 連線的上限。
    """

    def __init__(self, size: int | None = None) -> None:
        self._buf: deque[Event] = deque(maxlen=size or config.RING_BUFFER_SIZE)
        self._waiters: set[asyncio.Event] = set()
        # 本世代能發出的第一個序號。序號的高位是啟動時間戳，所以重啟後的序號
        # 一定大於上一世代——用這個把「服務重啟」跟「真的漏事件」分開。
        #
        # **建構時就從序號產生器取，不等第一次 publish。** 先前是 publish 才填，
        # 於是重啟後搶在任何事件之前重連的裝置（手機的 SSE 重連比回合快得多）
        # 帶著舊世代游標進來，這裡還是 None → 既不算 stale 也拿不到 stream.reset，
        # 前端以為自己收齊了，畫面停在上一世代。
        # 取走的那個號碼不會有任何事件用到，之後發的序號一律大於它，所以
        # 「< _boot_seq 就是舊世代」的判準要用「取走的號碼 + 1」。
        self._boot_seq: int = _seq.next() + 1

    def publish(self, ev: Event) -> None:
        """同步、不阻塞。emit 契約要求永不拖垮回合，所以這裡不做任何 IO。"""
        self._buf.append(ev)
        for w in list(self._waiters):
            w.set()

    @property
    def has_listeners(self) -> bool:
        """現在有沒有裝置連著 SSE。

        給「發請求出去等手機回」的路徑做快速失敗用：一個訂閱者都沒有時，
        那個請求註定等到逾時，不如當場承認手機不在線上。
        """
        return bool(self._waiters)

    @property
    def latest_seq(self) -> int | None:
        return self._buf[-1].seq if self._buf else None

    @property
    def oldest_seq(self) -> int | None:
        return self._buf[0].seq if self._buf else None

    def is_stale_cursor(self, after_seq: int | None) -> bool:
        """這個游標是不是上一個世代留下的（也就是中間服務重啟過）。

        序號高位是啟動時間戳，重啟後整批序號都會大於上一世代，比大小就分得出來。
        """
        return after_seq is not None and after_seq < self._boot_seq

    def replay_from(self, after_seq: int | None) -> tuple[list[Event], bool]:
        """取出 after_seq 之後的事件。

        回傳 (事件清單, 是否有斷層)。斷層＝要補的起點已經被 ring buffer 擠掉了，
        這時前端該改拉 snapshot 而不是假裝自己收齊了。

        **服務重啟既不算斷層、也不重播。** 這兩件事先前被混為一談：因為重啟不該
        誤報「離線太久」，就順手把整個 buffer 倒給對方——但那些事件屬於上一個世代，
        前端早就收過、也早就畫在畫面上了。前端沒有 seq 去重，於是整批被當成新事件
        重畫一次，使用者看到的就是**對話自己倒帶**：幾十分鐘前的訊息一則則重新冒出來，
        跟 snapshot 補回來的歷史疊在一起。2026-08-17 使用者回報「訊息回溯」。

        `RING_BUFFER_SIZE` 從 2000 提到 6000 之後這個症狀會嚴重三倍，
        所以修的是重播本身，不是 buffer 大小。

        正確行為是一則都不補：重啟後的權威來源是 snapshot（它直接讀 CC 的逐字稿），
        ring buffer 裡的上一世代事件沒有任何補充價值。呼叫端用 `is_stale_cursor`
        判斷要不要通知前端重新對齊。
        """
        if after_seq is None or self.is_stale_cursor(after_seq):
            return [], False
        gap = bool(self._buf) and self._buf[0].seq > after_seq + 1
        return [e for e in self._buf if e.seq > after_seq], gap

    async def stream(self, after_seq: int | None) -> AsyncIterator[Event | None]:
        """訂閱事件流。yield None 代表該送 keepalive 心跳了。

        心跳是必要的：OkHttp 預設 10 秒讀取逾時，而 CC 思考期間可能 30 秒沒有任何
        輸出，沒有心跳的話 client 會自己掐斷連線然後無限重連。
        """
        stale = self.is_stale_cursor(after_seq)
        pending, gap = self.replay_from(after_seq)
        cursor = after_seq
        if stale:
            # 服務重啟過。不補任何舊事件（見 replay_from），改叫前端以 snapshot 對齊，
            # 並把游標直接推到最新——否則下一圈的「待送檢查」會拿舊游標比對整個
            # buffer，等於繞過 replay_from 把重播原封不動做一次。
            yield make_event("", "", "stream.reset")
            cursor = self.latest_seq or 0
        elif gap:
            first = self._buf[0].seq if self._buf else 0
            yield make_event("", "", "seq.gap", **{"from": after_seq, "to": first})
        for e in pending:
            yield e
            cursor = e.seq
        if cursor is None:
            cursor = self.latest_seq or 0

        signal = asyncio.Event()
        self._waiters.add(signal)
        try:
            while True:
                # 順序很重要：先 clear、再檢查待送、最後才等。
                #
                # 早期版本是「clear → wait → yield」，結果 yield 期間 publish 的事件
                # 會 set() 這個旗標，卻被下一圈開頭的 clear() 清掉，那則事件就要等滿
                # 15 秒心跳才補送。實際症狀是 reply.final（緊跟在 turn.end 後幾毫秒）
                # 送不到手機——整個回合看起來只差最後一則、最重要的那則。
                #
                # 現在的順序沒有漏窗：publish 是「先 append 再 set」，所以
                #   - clear 之後才 append → 下面的待送檢查會抓到
                #   - 還沒 append → 待送為空進入 wait，之後的 set 會叫醒它（Event 是黏著的）
                signal.clear()
                pending = [x for x in self._buf if x.seq > cursor]
                if pending:
                    for e in pending:
                        yield e
                        cursor = e.seq
                    continue
                try:
                    await asyncio.wait_for(signal.wait(), timeout=config.SSE_KEEPALIVE_SEC)
                except asyncio.TimeoutError:
                    yield None          # 心跳
        finally:
            self._waiters.discard(signal)


class SseFrontend:
    """protocol.Frontend 的 SSE 實作，一個對話一個實例。

    engine 只看得到 Frontend 這個介面，完全不知道 HTTP 或 SSE 的存在。
    """

    def __init__(self, hub: EventHub, conv_id: str) -> None:
        self._hub = hub
        self._conv = conv_id
        self._cur_turn = ""
        # 連問題內容一起留著，不是只留一個「有人在等」的 Future。
        # snapshot 要能把未決的提問交出去（見 pending_asks），只有 Future 是給不出
        # 內容的——而給不出內容的下場就是提問卡片從畫面上消失、伺服器繼續空等。
        self._pending: dict[str, tuple[AskRequest, asyncio.Future[AskResponse]]] = {}

    async def emit(self, ev: Event) -> None:
        """單向送事件。永不拋例外——前端的任何問題都不可以拖垮正在跑的回合。"""
        try:
            if ev.type == "turn.start":
                self._cur_turn = ev.turn_id
            self._hub.publish(ev)
        except Exception:
            pass

    async def ask(self, req: AskRequest) -> AskResponse | None:
        """提問並等答案。逾時回 None，呼叫端一律 fail-closed 當作拒絕。"""
        ask_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future[AskResponse] = asyncio.get_running_loop().create_future()
        self._pending[ask_id] = (req, fut)
        await self.emit(make_event(
            self._conv, self._cur_turn, "ask.request",
            ask_id=ask_id,
            kind=req.kind,
            title=req.title,
            body=req.body,
            raw=req.raw,                      # 指令原文全文，前端必須完整顯示
            require_biometric=req.require_biometric,
            timeout_sec=req.timeout_sec,
            choices=[{"id": c.id, "label": c.label, "detail": c.detail}
                     for c in req.choices],
        ))
        # answer 要拉到 try 外面：ask.resolved 得說出「最後選的是哪個」，
        # 手機才能把對話裡那張提問卡改成「你選了 ○○」。
        # 逾時或被取消時維持空字串，前端據此顯示「沒回答」。
        answer: AskResponse | None = None
        try:
            answer = await asyncio.wait_for(fut, timeout=req.timeout_sec)
            return answer
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None
        finally:
            self._pending.pop(ask_id, None)
            await self.emit(make_event(
                self._conv, self._cur_turn, "ask.resolved",
                ask_id=ask_id,
                choice_id=answer.choice_id if answer is not None else "",
            ))

    def resolve(self, ask_id: str, choice_id: str) -> bool:
        """由 HTTP 端點呼叫，把答案交回等待中的 ask。回傳是否成功對上。"""
        entry = self._pending.get(ask_id)
        if entry is None or entry[1].done():
            return False
        entry[1].set_result(AskResponse(choice_id=choice_id))
        return True

    def pending_asks(self) -> list[dict[str, Any]]:
        """還在等答案的提問，格式對齊 `ask.request` 事件。

        給 snapshot 用。提問卡片是 butler 自己造的，CC 的逐字稿裡沒有它，所以
        App 一重建畫面（重裝、被系統回收、stream.reset）卡片就消失了——而伺服器
        還在等，最多 timeout_sec 秒，等不到就 fail-closed 當成使用者拒絕。
        前三個同類 bug 只是「看不到但事情照跑」，這個是「看不到，然後那件事被
        當成你拒絕了」，使用者從頭到尾不知道它問過。
        """
        return [
            {
                "ask_id": aid,
                "kind": req.kind,
                "title": req.title,
                "body": req.body,
                "raw": req.raw,
                "require_biometric": req.require_biometric,
                "choices": [
                    {"id": c.id, "label": c.label, "detail": c.detail}
                    for c in req.choices
                ],
            }
            for aid, (req, fut) in self._pending.items()
            if not fut.done()
        ]
