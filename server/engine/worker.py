"""每條對話一個回合工作者：排隊、合併、插話、停止、wake 回合、限流自動續跑。

這一段原本長在 transport/app.py 裡。2026-09-03 抽出來的理由只有一個：Discord 那邊的
cc-bot 也要用同一套「一條對話一個 worker」的規矩，而它不跑 FastAPI。做成類別而不是
模組全域，是讓兩個前端各自持有一份、互不干擾；一個進程裡照樣只需要一個實例。

它認得的東西全在 engine 與 protocol 裡：`turn.handle_turn`／`handle_wake` 跑回合、
`runner.try_steer` 插話、`bg_notify` 收背景工作卡片、`mailbox.WakeTicket` 排 wake。
前端只需要提供一件事——`frontend_for(conv_id)` 給它一個 `protocol.Frontend`——
外加一個可有可無的自動命名回呼。

`handle_turn`／`handle_wake` 以模組層名稱綁進來、在呼叫點才查：tests/ 對
`engine.worker.handle_turn` monkeypatch 就能換成假回合，不燒 API、不佔埠。
`asyncio.sleep` 同理（限流等待的測試把它縮短）。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import config
from protocol import Frontend, make_event
from util import atomic_write_text, read_text_with_retry

from . import bg_notify
from . import runner as engine_runner
from . import state as state_mod
from .errors import AUTO_RESUME_MAX_SEC, reset_label
from .mailbox import WakeTicket
from .state import get_state
from .turn import handle_turn, handle_wake, stamp

# 佇列裡放的是 (msg_id, text, src)：手機端要能把「還排著」與「已經在做」畫成兩個樣子，
# 就得指名道姓說出是哪幾則被讀走了，光靠則數對不起來。
# src 是這則話從哪一端送進來的（「手機」／「電腦」／「Discord」），由 `turn._stamp`
# 蓋進時間戳。**不在這裡就把它拼進 text**：那個字串同時是回音事件與 `pending_of`
# 的內容，拼進去就會出現在使用者自己的氣泡上。
#
# 同一條佇列裡還會有 `Wake`：CLI 自己開了一個模型週期（背景工作跑完後原生的
# 那一輪），mailbox 發的票排在這裡等 worker 接手。跟使用者訊息排同一條是為了
# 序列化——同一時間只能有一個回合在讀收件匣，而 worker 正是那個唯一的執行者。


@dataclass(frozen=True, slots=True)
class Wake:
    """佇列裡的一張 wake 票（見 `Worker.dispatch_wake`）。"""

    ticket: WakeTicket


# (msg_id, 原文, 來源, 附件)。附件是結構化欄位而不是拼進原文的路徑——
# 畫面照它畫縮圖與檔案卡，只有送給模型的那一份才把路徑接上去（見 turn.stamp）。
QueueItem = tuple[str, str, str, list[dict]] | Wake


@dataclass(frozen=True, slots=True)
class Submitted:
    """`Worker.submit` 的結果：這則訊息被怎麼安置了。"""

    msg_id: str
    # 進佇列等（True），還是已經插進進行中的回合（False 且 steered=True）或立刻開跑
    queued: bool
    steered: bool
    qsize: int


_MERGE_HEADER = (
    "以下是我在你忙的時候連續傳的幾則訊息，請當成同一件事一起處理。"
    "注意：後面的訊息很可能是在修正或補充前面的，不要當成幾件平行任務各做一遍。\n\n"
)


async def _take_batch(q: asyncio.Queue[QueueItem]) -> list[QueueItem]:
    """取出這一輪要處理的所有訊息：先等第一則，再把當下佇列裡的使用者訊息清空。

    刻意不設則數上限——訊息被收下卻不處理，比排隊太長更傷。

    **wake 自成一批，不跟使用者訊息混。** 先前一批裡 wake 與訊息並存，停掉
    wake 回合時 `except CancelledError` 只處理佇列裡的東西，同批裡還沒輪到的
    訊息既沒跑也沒報 dropped——人送的話就這樣無聲消失。拿到 wake 就只回它；
    拿到訊息就往後收，收到下一個 wake 為止（它留在佇列裡，下一批再拿）。
    """
    first = await q.get()
    if isinstance(first, Wake):
        return [first]
    items: list[QueueItem] = [first]
    # 偷看隊首而不取走：asyncio.Queue 沒有 peek，讀內部 deque 的先例見 pending_of
    while not q.empty() and not isinstance(q._queue[0], Wake):
        items.append(q.get_nowait())
    return items


def _merge(items: list[str]) -> str:
    """把整批訊息合併成一次 prompt（對照 cc-bot 的 _merge_queued）。

    那句「後面的可能在修正前面的」是關鍵：少了它，模型會把幾則訊息當成
    幾件平行任務全做一遍，而人在等待時補的話多半是更正而不是加碼。
    """
    if len(items) == 1:
        return items[0]
    body = "\n\n".join(f"（第 {i} 則）{t}" for i, t in enumerate(items, 1))
    return _MERGE_HEADER + body


class Worker:
    """所有對話的回合工作者集合：每條對話一條佇列、一個讀它的 task。

    三份登記表（`queues`／`workers`／`running`）刻意公開：snapshot 要讀排隊中的
    訊息、控制台要知道哪幾條在忙、tests/ 要直接對它們動手。改它們的只有這個類別。
    """

    def __init__(
        self,
        frontend_for: Callable[[str], Frontend],
        *,
        pending_file: Path | None = None,
        autoname: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        # 公開屬性：測試把它換成回傳假前端的函式，其餘時候是前端的查表函式
        self.frontend_for = frontend_for
        # 限流等待中的訊息落檔處（見 `_pending_remember`）
        self.pending_file: Path = (
            pending_file if pending_file is not None
            else config.DATA_DIR / "pending_resume.json"
        )
        # (conv_id, 第一則文字)：回合結束後在背景生標題。None＝這個前端不自動命名
        self._autoname = autoname
        self.queues: dict[str, asyncio.Queue[QueueItem]] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.running: dict[str, asyncio.Task] = {}   # 進行中的回合，供停止與插話認

    # ── 進出口 ─────────────────────────────────────────────────────────────
    def ensure(self, conv_id: str) -> asyncio.Queue[QueueItem]:
        """該對話的佇列，順便確保有一個活的 worker 在讀它。

        worker 若已結束（handle_turn 之外漏網的例外會讓它整個死掉）就重建。先前只在
        佇列不存在時才建 worker，worker 死了佇列還在，之後每一則訊息都排進去、
        永遠沒人讀，對外症狀只是「助理不回了」。
        """
        q = self.queues.get(conv_id)
        if q is None:
            q = self.queues[conv_id] = asyncio.Queue()
        w = self.workers.get(conv_id)
        if w is None or w.done():
            self.workers[conv_id] = asyncio.create_task(self._loop(conv_id))
        return q

    async def submit(self, conv_id: str, text: str, src: str,
                     attachments: list[dict] | None = None) -> Submitted:
        """收一則使用者訊息：回合進行中就插話，否則排隊；一律先發回音事件。

        `src` 已經是中文來源名（「手機」「電腦」「Discord」），對照表由各前端自己管。
        """
        fe = self.frontend_for(conv_id)
        msg_id = uuid.uuid4().hex[:12]
        # 完成的背景工作卡片收起來：他已經看過結果、已經接著講下一件事了，
        # 那幾張卡片再留在畫面上只是擋路。進行中的不動。
        if bg_notify.clear_done(conv_id):
            await fe.emit(bg_notify.bg_event(conv_id))
        q = self.ensure(conv_id)
        # 回合進行中優先「插話」：直接注入正在跑的回合，模型下一步決策就看得到，
        # 不必等回合結束（與官方終端機邊跑邊打字同一條路，見 engine.runner）。
        # 佇列裡已有人排就不插——插了等於跳過前面的訊息，順序會反過來。
        steered = False
        if conv_id in self.running and q.empty():
            # 插進去的也是人說的話，跟排隊那條一樣蓋時間戳與來源；不蓋的話模型
            # 看到的是一則沒時間、沒來源的訊息（2026-09-02 審查抓到）
            steered = await engine_runner.try_steer(
                conv_id, stamp(text, src, attachments))
        # 這則是不是要排隊，只有伺服器知道：手機端的 busy 是上一則 status 的殘影，
        # 而回合停在提問上等人回答時它甚至不算忙，訊息卻照樣排進來乾等。
        queued = (not steered) and (conv_id in self.running or not q.empty())
        # 回音事件：讓「別台裝置送的訊息」也出現在所有畫面上。
        # App 端一律以這個事件為準渲染使用者訊息（自己送的不在本地先畫），
        # 多裝置同步因此不需要任何額外機制。插話過的 queued=False——它已經在
        # 當前回合裡被讀走了，畫成「排隊中」是謊報。
        await fe.emit(make_event(
            conv_id, "-", "user.message", text=text, msg_id=msg_id, queued=queued,
            steered=steered, attachments=list(attachments or ()),
        ))
        # 插話過的不進佇列：進了就會在回合結束後被 worker 再跑一次，同一句話
        # 處理兩遍。
        if not steered:
            await q.put((msg_id, text, src, list(attachments or ())))
        return Submitted(msg_id=msg_id, queued=queued, steered=steered, qsize=q.qsize())

    def pending_of(self, conv_id: str) -> list[dict[str, str]]:
        """佇列裡還沒被讀走的訊息，舊到新。

        直接讀 asyncio.Queue 的內部 deque。這是私有屬性，但另外維護一份鏡像就得在
        送出、讀進回合、按停止取消三個地方同步，任何一處漏掉都會讓畫面跟真相對不上
        ——而那正是這個函式要修的 bug，用一個會不同步的東西去修它沒有道理。
        寧可依賴一個穩定的實作細節。

        為什麼 snapshot 需要它：排隊中的訊息還沒送進 CC，所以逐字稿裡沒有它。
        App 一重建畫面（重裝、被系統回收、stream.reset）就是從逐字稿重讀，
        那則訊息於是從畫面上消失，但伺服器照樣會處理它——使用者 2026-08-18 回報
        「我這邊看不到我傳的訊息了，但它實際還排在那邊」。
        """
        q = self.queues.get(conv_id)
        if q is None:
            return []
        # 排著的 wake（助理自己要醒來的那一輪）不列。這份清單畫的是「你送出但還沒
        # 輪到的訊息」，混進一則人沒打過的東西就是謊報。
        return [
            {"msg_id": it[0], "text": it[1], "attachments": it[3] if len(it) > 3 else []}
            for it in list(q._queue) if not isinstance(it, Wake)
        ]

    def is_running(self, conv_id: str) -> bool:
        """這條對話現在有沒有回合在跑（含限流等待中的那個 sleep）。"""
        task = self.running.get(conv_id)
        return task is not None and not task.done()

    def stop(self, conv_id: str) -> bool:
        """使用者按了停止：取消進行中的那一輪。沒有東西在跑就回 False。

        只取消 `running` 那個 task，worker 本身活著、發 STOPPED、繼續讀下一批
        （取消語意的兩種分法見 `_loop`）。
        """
        task = self.running.get(conv_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def remove(self, conv_id: str) -> None:
        """對話被刪：停掉進行中的回合、收掉 worker、丟掉佇列。

        frontend 的登記、client 進程、對話狀態檔仍由呼叫端各自收——那幾樣不歸
        這裡管。等 worker 真的結束才回來，呼叫端接著做的事才不會跟它的收尾打架。
        """
        task = self.running.get(conv_id)
        if task and not task.done():
            task.cancel()
        w = self.workers.pop(conv_id, None)
        self.queues.pop(conv_id, None)
        if w is not None:
            w.cancel()
            # wait 而不是直接 await：被取消的 task 直接 await 會把 CancelledError
            # 丟到這裡來，而那是它的取消，不是我們的
            await asyncio.wait({w})

    def dispatch_wake(self, conv_id: str, frontend: object, ticket: WakeTicket) -> None:
        """CLI 自己開了一個模型週期（背景工作跑完後原生的那一輪）：排進該對話的
        佇列，輪到就由 worker 接手成 wake 回合（見 engine.bg_notify 與 mailbox）。

        刻意走跟使用者發言完全相同的那條佇列——同一個 worker、同一組並行控制。
        這樣「助理正在忙」「插話」「按停止」全部自動適用，不必為它另寫一套。

        簽章對齊 `bg_notify.Waker`。`frontend` 這裡用不到：worker 自己用
        `frontend_for` 查，跟使用者回合拿到的是同一個。

        **同步、不 await。** mailbox 從「開收件匣」到「派工」之間不能讓出控制權，
        否則 pump 會把週期的下一則投到 idle。`ensure` 與 `put_nowait` 都不會讓出。

        這裡就是 2026-08-25 到 2026-09-02 之間 `_resume_after_bg` 的位置：那時
        butler 自己排一則中文散文當使用者訊息把助理叫醒，而 CLI 原生那一輪被整段
        丟掉——同一件事跑兩遍。使用者 2026-09-02：「走訊息通知太潦草了」。
        """
        q = self.ensure(conv_id)
        q.put_nowait(Wake(ticket))

    async def shutdown(self) -> None:
        """服務收工：取消所有 worker，等它們真的結束。"""
        tasks = [t for t in self.workers.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks)

    # ── 限流等待中的訊息落檔 ──────────────────────────────────────────────
    # {conv, text, src} 的清單。等待是記憶體裡的一個 sleep，服務重啟就沒了；
    # 人送出去的話不能因為額度用完又剛好重啟而人間蒸發。
    def _pending_load(self) -> list[dict]:
        try:
            data = json.loads(read_text_with_retry(self.pending_file))
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001 — 沒有檔、壞掉的檔都當成沒有
            return []

    def _pending_save(self, items: list[dict]) -> None:
        try:
            atomic_write_text(self.pending_file, json.dumps(items, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    def _pending_remember(self, conv_id: str, text: str, src: str,
                          attachments: list[dict] | None = None) -> None:
        items = [i for i in self._pending_load() if i.get("conv") != conv_id]
        # 附件一起留著：只存文字的話，額度回復後重跑的那一輪會少掉檔案，
        # 模型看到「看這張圖」卻沒有圖
        items.append({"conv": conv_id, "text": text, "src": src,
                      "attachments": list(attachments or ())})
        self._pending_save(items)

    def _pending_forget(self, conv_id: str) -> None:
        items = [i for i in self._pending_load() if i.get("conv") != conv_id]
        self._pending_save(items)

    async def restore_pending(self) -> int:
        """啟動時把上次限流等待中的訊息重新排回佇列。回傳排回幾則。

        不等回復時刻，直接排：額度真的還沒回來的話 handle_turn 會再撞一次限流、
        走同一條自動等待，最多多付一次被拒的呼叫；比自己在這裡再算一次時刻穩。
        """
        items = self._pending_load()
        if not items:
            return 0
        self._pending_save([])
        for it in items:
            conv = str(it.get("conv") or "")
            text = str(it.get("text") or "")
            if not conv or not text:
                continue
            q = self.ensure(conv)
            atts = it.get("attachments")
            await q.put((uuid.uuid4().hex[:12], text, str(it.get("src") or ""),
                         list(atts) if isinstance(atts, list) else []))
        return len(items)

    # ── 回合執行 ───────────────────────────────────────────────────────────
    async def _run_messages(
        self, conv_id: str, fe: Frontend, batch: list[tuple[str, str, str, list[dict]]],
    ) -> None:
        """把一批使用者訊息合併成一輪跑完。佇列的 task_done 由 `_loop` 統一做。"""
        # 這幾則從「排著」變成「正在做」。手機端靠這則事件把淡掉的氣泡點亮，
        # 少了它，畫面上永遠分不出助理讀到哪一則了。
        await fe.emit(make_event(
            conv_id, "-", "message.taken", msg_ids=[b[0] for b in batch],
        ))
        text = _merge([b[1] for b in batch])
        # 整批的附件併起來：一批就是一輪，模型讀到的是同一則 prompt
        atts = [a for b in batch for a in (b[3] or ())]
        # 一批裡混到兩種來源是罕事（人不會同時拿著手機又坐在電腦前打字），
        # 真混到就聽最後一則的——那則最接近「他現在人在哪」
        src = batch[-1][2]
        state = get_state(conv_id)
        need_title = self._autoname is not None and state_mod.get_title(conv_id) is None
        if len(batch) > 1:
            await fe.emit(make_event(
                conv_id, "-", "status", note=f"把剛才那 {len(batch)} 則一起處理",
            ))
        task = asyncio.create_task(handle_turn(text, state, fe, src, atts))
        self.running[conv_id] = task
        err = await task
        if err is not None and err.kind == "RATE_LIMIT" and err.resets_at:
            # 額度用盡：訊息不丟，等到 CLI 說的回復時刻自動再跑一次（只等一次，
            # 再撞就交給人）。等待期間 `running` 指著 sleep 那個 task，所以按停止
            # 停得掉、新送來的訊息會照規矩排隊在後面。
            wait = err.resets_at - time.time()
            if 0 < wait <= AUTO_RESUME_MAX_SEC:
                await fe.emit(make_event(
                    conv_id, "-", "status",
                    note=f"額度用完了，{reset_label(err.resets_at)} 自動繼續",
                    phase="waiting",
                ))
                # 等待期間服務重啟（使用者按重新啟動、當機）會把佇列連同這則一起弄丟。
                # 先落檔，啟動時 `restore_pending` 會把它重新排回去
                self._pending_remember(conv_id, text, src, atts)
                try:
                    waiter = asyncio.create_task(asyncio.sleep(wait + 5))
                    self.running[conv_id] = waiter
                    await waiter
                    task = asyncio.create_task(handle_turn(text, state, fe, src, atts))
                    self.running[conv_id] = task
                    await task
                finally:
                    self._pending_forget(conv_id)
        if need_title:
            # 標題放在回合完成後才生：第一回合常伴隨 client 建立，
            # 同時再開一個 Haiku 進程會讓首則回覆變慢
            # batch 裡放的是 (msg_id, text, src)，取文字那半——
            # 直接傳整個 tuple 會在背景任務裡炸 AttributeError，
            # 而且背景例外不會冒到請求端，只會默默留在 log 裡：
            # 症狀是「標題永遠生不出來」，看起來像 Haiku 沒回應。
            asyncio.create_task(self._autoname(conv_id, batch[0][1]))

    async def _loop(self, conv_id: str) -> None:
        """單一對話的回合工作者：忙碌時收下訊息，回合結束後一次全讀、合併成一輪。

        重試、續跑、壓縮、錯誤善後全在 `engine.turn.handle_turn` 裡，
        這裡只負責排隊與「使用者按了停止」這一種前端才知道的狀況。

        佇列裡的 `Wake`（CLI 自己開的週期，見 `dispatch_wake`）也在這裡接，
        走 `engine.turn.handle_wake`。wake 自成一批、不跟訊息混（見 `_take_batch`），
        所以下面的 wakes／msgs 兩個清單同一時間只會有一個非空。wake 回合一樣登記進
        `running`，所以回合進行中送來的訊息會走插話、按停止也停得掉。
        """
        fe = self.frontend_for(conv_id)
        q = self.queues[conv_id]
        while True:
            batch = await _take_batch(q)
            wakes = [b for b in batch if isinstance(b, Wake)]
            msgs = [b for b in batch if not isinstance(b, Wake)]
            try:
                for w in wakes:
                    task = asyncio.create_task(handle_wake(w.ticket, get_state(conv_id), fe))
                    self.running[conv_id] = task
                    await task
                if msgs:
                    await self._run_messages(conv_id, fe, msgs)
            except asyncio.CancelledError:
                # 兩種取消要分開：
                #   - 停這一輪（`stop` 取消 `running[conv]`）：CancelledError 是從
                #     `await task` 冒上來的，worker 本身沒被取消 → 走下面的 STOPPED。
                #   - 停 worker（`remove`、`shutdown`）：worker 自己被 cancel。
                #     先前這條也走 STOPPED：對著已 pop 的 frontend 發事件，然後回到
                #     `while True` 繼續等一個已經沒人放東西的孤兒佇列，永遠不死。
                # `cancelling()` 只算 worker 自己收到的取消請求，內層 task 的取消不算。
                cur = asyncio.current_task()
                if cur is not None and cur.cancelling():
                    raise
                # 停止時把還排著的一起丟掉，並更正提示——留著「已排隊 N 則」
                # 會讓人以為還排著，實際上已經不會處理了
                dropped: list[str] = []
                # 這一批裡還沒輪到的 wake 也一起作廢——它預開的收件匣不收掉，
                # 下一個回合會把它認養走、把那個早就結束的週期當成自己的回覆
                for w in wakes:
                    await w.ticket.discard()
                while not q.empty():
                    item = q.get_nowait()
                    q.task_done()
                    if isinstance(item, Wake):
                        await item.ticket.discard()
                        continue
                    dropped.append(item[0])
                detail = "好，我停下了。"
                if dropped:
                    detail += f"排著的 {len(dropped)} 則也一起取消了。"
                    # 那幾則氣泡得從「排隊中」改成「已取消」——留著排隊中的樣子，
                    # 人會一直等一件永遠不會發生的事。
                    await fe.emit(make_event(
                        conv_id, "-", "message.dropped", msg_ids=dropped,
                    ))
                await fe.emit(make_event(
                    conv_id, "-", "error", kind="STOPPED",
                    detail=detail, retryable=False,
                ))
            finally:
                self.running.pop(conv_id, None)
                for _ in batch:
                    q.task_done()
