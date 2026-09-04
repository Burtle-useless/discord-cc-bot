"""對話層的訊息收發中樞：唯一的讀取者，把訊息分派給回合、wake 回合或回合外處理器。

butler 原本讓「回合」自己去讀 SDK 的訊息串流：`run_turn` 開始讀、收到
ResultMessage 就 break 走人。這個模型有一個沒有出口的漏洞——**回合之外
也會有訊息**。丟到背景的工作（`run_in_background` 的指令、Task 子代理）
跑完時，CLI 會送一則 `task_notification` 進來，而那時早就沒有人在讀了。

沒人讀不代表訊息消失：它留在 anyio 的接收緩衝裡，被下一則使用者訊息的回合
當成自己的回覆讀走（問 A 卻回上一件事的 B）。舊做法是收工時偵測到還有背景
工作就把整個 client 丟掉（disconnect），用斬斷連線換乾淨——代價是那則通知
連同它帶的摘要與輸出檔路徑一起蒸發，使用者永遠不會知道他丟到背景的東西跑完了。
2026-08-23 使用者回報「我們好像忘了做自動喚醒」講的就是這件事：那天腳本
09:58 就跑完並寫好了輸出檔，助理 09:57 收工，沒有任何人回頭看一眼。

這一層把「誰在讀」從回合提升到對話：**唯一的讀取者常駐在這裡**，回合開始時
登記自己是收件人、結束時交還。歸屬因此永遠明確——沒有收件人的時候進來的訊息
就是回合外事件，交給 idle 處理器（見 `bg_notify`）。連線不必再丟，session
不會斷，也不會再有讀錯回覆的風險，因為訊息從頭到尾只有一個讀者。

**回合外的模型活動是 wake 回合，不是雜訊。** 背景工作跑完之後 CLI 不只送通知，
它會**自己另起一個模型週期**：把 `<task-notification>` 注入、讓模型讀結果、
接著把話講完——跟官方終端機裡「背景工作跑完助理自己醒來接著講」是同一件事
（實測 bundled CLI 2.1.247，2026-09-02：通知後 0.8 秒串流就開始，單一 Result；
週期進行中再 `query()` 會被插進同一週期）。2026-09-02 之前這整個週期落到 idle
處理器被逐則丟掉，butler 再自己排一則中文散文當使用者訊息把模型叫醒**第二次**
——同一件事跑兩遍，使用者看到的是第二遍。現在的規則：沒有回合在收件時只要
看到模型活動（串流事件或 AssistantMessage），當場開一個收件匣接住它、發一張
`WakeTicket` 給 transport 排成 wake 回合。開收件匣這一步是同步的，訊息一則都
不會漏到 idle。

單一讀取者不是設計偏好而是硬限制：`_query.receive_messages()` 背後是一條
anyio memory stream，每則訊息只有一個讀者拿得到，兩處併發讀會互相搶。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, StreamEvent
from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal.message_parser import parse_message

from protocol import Frontend

log = logging.getLogger(__name__)

# 串流正常結束的哨兵。用獨立物件而不是 None：None 是合法的訊息值。
_EOF: Any = object()

# 回合外訊息的處理函式：(對話 id, 該對話的 frontend, 訊息)
IdleHandler = Callable[[str, Frontend, Any], Awaitable[None]]

# wake 回合的派工函式：(對話 id, frontend, 票)。**同步**、回傳有沒有接下——
# 沒人接（例如測試裡沒掛 transport）就退回 idle 處理，收件匣不能開了沒人讀。
# 同步是刻意的：開收件匣到派工之間不能有 await，否則 pump 會把下一則投到 idle。
WakeHandler = Callable[[str, Frontend, "WakeTicket"], bool]


class MailboxClosed(Exception):
    """串流已正常結束（EOF）。回合看到它就收工，交給既有的收尾流程。"""


class WakeConsumed(Exception):
    """這張 wake 票已經失效：收件匣被使用者回合認養走了，或連線已經不在。

    拿到它的 wake 回合什麼都不必做——那個週期已經有人在讀（認養它的回合），
    或者早就沒東西可讀。
    """


def is_model_activity(msg: Any) -> bool:
    """這則訊息代表模型正在說話或做事嗎。

    只認串流事件與 AssistantMessage。SystemMessage（init／status／thinking_tokens）
    在每個週期開頭都會出現，但它們也出現在跟模型無關的地方（連線建立、hook），
    拿它們當觸發會開出空的收件匣；ResultMessage 單獨出現則是「週期已經結束」，
    沒有東西可接。
    """
    return isinstance(msg, (StreamEvent, AssistantMessage))


class _Inbox:
    """回合這一側的收件把手。

    兩種取法等價：`async for` 逐則讀（既有用法），或 `get(timeout)` 帶逾時讀。
    後者是插話觀察窗要用的：插話撲空時 CLI 會自行另開一個回應週期，回合得在
    自己的 ResultMessage 之後多等幾秒看有沒有孤兒週期冒出來，等不到要能自己
    醒來收工——阻塞式的 `__anext__` 做不到這件事。
    """

    def __init__(self, q: "asyncio.Queue[Any]") -> None:
        self._q = q

    async def get(self, timeout: float | None = None) -> Any:
        """取下一則訊息。串流結束拋 MailboxClosed；逾時拋 asyncio.TimeoutError；
        串流錯誤原地重拋，交給既有的錯誤善後。"""
        if timeout is None:
            item = await self._q.get()
        else:
            item = await asyncio.wait_for(self._q.get(), timeout)
        if item is _EOF:
            raise MailboxClosed()
        if isinstance(item, BaseException):
            raise item
        return item

    def __aiter__(self) -> "_Inbox":
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.get()
        except MailboxClosed:
            raise StopAsyncIteration from None


class WakeTicket:
    """一張「CLI 自己開了一個週期，收件匣已經幫你開好」的票。

    由 `Mailbox._deliver` 在回合外看到模型活動時發出，交給 transport 排成
    wake 回合；wake 回合拿它 `claim(ticket=...)` 接手那個收件匣。票只在收件匣
    還沒被別人認養時有效（見 `pending`）。
    """

    __slots__ = ("box", "q")

    def __init__(self, box: "Mailbox", q: "asyncio.Queue[Any]") -> None:
        self.box = box
        self.q = q

    def pending(self) -> bool:
        """收件匣還在等這張票的主人來認領嗎。"""
        return self.box._wake is self.q and self.box._inbox is self.q

    async def discard(self) -> None:
        """這張票不會有人來領了（排隊時被停止、對話被刪），把收件匣收掉。

        沒人領的收件匣會一直吃 pump 投進來的訊息，而下一個使用者回合會把它
        認養走——裡面那個早就結束的週期就被當成那個回合的回覆讀掉（問 A 回 B，
        2026-08-14 那種失憶事件的同一塊地雷）。所以要明確收掉：裡面的東西改走
        idle 處理（終結通知照樣結帳），模型活動則直接丟，不再開新票。
        """
        if not self.pending():
            return
        self.box._inbox = None
        self.box._wake = None
        while not self.q.empty():
            item = self.q.get_nowait()
            if item is _EOF or isinstance(item, BaseException) or is_model_activity(item):
                continue
            try:
                await self.box._on_idle(self.box.conv_id, self.box.frontend, item)
            except Exception:  # noqa: BLE001
                log.exception("丟棄 wake 收件匣時處理訊息失敗（對話 %s）", self.box.conv_id)


async def iter_messages(client: ClaudeSDKClient) -> AsyncIterator[Any]:
    """逐一取出 client 的訊息並解析成 SDK 物件；解析失敗的直接跳過。

    集中存取 SDK 私有介面（`_query.receive_messages`）的**唯一入口**：公開 API
    遇到 MessageParseError 會中斷整條串流，這裡改為跳過壞訊息，維持長任務的韌性。
    SDK 升版若動到私有介面，只需要修這一個函式。

    對話的長駐 client 不該直接用它——那是 `Mailbox` 的專屬領地，兩個讀者會搶訊息。
    這裡公開出來是給**一次性 client** 用的（`meta.ask_haiku` 那種自己建、用完就丟、
    全程只有一個讀者的場合）。
    """
    async for raw in client._query.receive_messages():
        try:
            yield parse_message(raw)
        except MessageParseError:
            continue


class Mailbox:
    """一個對話的訊息收發中樞。包住一個長駐 client，獨佔它的讀取權。

    生命週期跟著 client 走：由 `client_pool` 建立與關閉，回合只透過
    `claim()` 借用收件匣。
    """

    def __init__(
        self,
        conv_id: str,
        client: ClaudeSDKClient,
        frontend: Frontend,
        on_idle: IdleHandler,
        on_wake: WakeHandler | None = None,
    ) -> None:
        self.conv_id = conv_id
        self.client = client
        # 回合外事件要送到畫面上，得有個出口。frontend 可寫是因為它由呼叫端
        # 每次帶進來，而 mailbox 活得比單一回合久（實務上 SseFrontend 是
        # 每個對話一份且被快取，換來換去都是同一個物件，但不能靠這個假設）。
        self.frontend = frontend
        self._on_idle = on_idle
        self._on_wake = on_wake
        self._inbox: asyncio.Queue[Any] | None = None
        # 幫 wake 回合預開的收件匣：跟 `_inbox` 是同一個物件，直到票被認領
        # （wake 回合或認養它的使用者回合）就歸零。
        self._wake: asyncio.Queue[Any] | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """啟動常駐讀取者。重複呼叫無害。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        """唯一的讀取者。從連上到關閉為止一直跑，不隨回合起落。"""
        try:
            async for msg in iter_messages(self.client):
                await self._deliver(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 串流的任何故障都要傳給等待中的回合
            await self._deliver(e)
        else:
            await self._deliver(_EOF)

    async def _deliver(self, item: Any) -> None:
        """投遞一則訊息：有回合在收就給回合；模型自己開口就開 wake 收件匣；
        其餘走回合外處理。"""
        q = self._inbox
        if q is not None:
            q.put_nowait(item)
            return
        if item is _EOF or isinstance(item, BaseException):
            # 回合外的串流結束／錯誤沒有人需要被通知：這個 client 已經不能用了，
            # 下次 acquire 會發現 task 已死而重建。這裡多做事只會製造假錯誤。
            return
        if self._on_wake is not None and is_model_activity(item):
            # **先開收件匣、再派工，中間沒有 await**：從這一行起 pump 投來的
            # 每一則都進這個匣子，wake 回合晚幾個 tick 才來讀也一則不漏。
            wq: asyncio.Queue[Any] = asyncio.Queue()
            wq.put_nowait(item)
            self._inbox = wq
            self._wake = wq
            if self._on_wake(self.conv_id, self.frontend, WakeTicket(self, wq)):
                return
            # 沒人接這張票：收回匣子，這則照舊走 idle
            self._inbox = None
            self._wake = None
        try:
            await self._on_idle(self.conv_id, self.frontend, item)
        except Exception:  # noqa: BLE001 — 回合外處理失敗不可以拖垮讀取迴圈
            log.exception("回合外訊息處理失敗（對話 %s）", self.conv_id)

    @contextlib.asynccontextmanager
    async def claim(self, ticket: WakeTicket | None = None) -> AsyncIterator[_Inbox]:
        """回合登記收件，取得一個只屬於自己的收件把手（可迭代、可帶逾時讀）。

        帶 [ticket] 是 wake 回合：接手 `_deliver` 預開的那個收件匣。票已失效
        （被使用者回合認養、或收件匣早就關了）就拋 WakeConsumed，呼叫端什麼都
        不必做。

        不帶票是一般回合。此時若正好有一張票還沒人領（CLI 自己開了週期、wake
        回合還沒排到），**這個回合就直接認養那個收件匣**：它接下來送的 prompt
        會被 CLI 插進正在跑的那個週期（實測如此，單一 Result），週期裡的訊息
        由它讀走正好。硬拒絕的話，這兩個回合會在同一個週期上互搶。

        離開時交還收件匣，並把**還沒讀完的訊息重新投遞**。這一步不是錦上添花：
        回合在收到 ResultMessage 之後到離開這個 context 之間還有好幾個 await
        （定稿事件、問 context 用量），pump 可能已經又投了幾則進來——而那裡
        正是背景工作完成通知最可能落腳的位置。直接丟掉就等於回到舊行為。
        重新投遞走的是 `_deliver`，所以殘留的模型活動會開成新的 wake 收件匣，
        不會被丟到 idle。

        同一時間只能有一個回合持有收件匣。同一對話的訊息本來就是序列化處理的
        （transport 層每個對話一條 worker），這裡把那個前提變成會出聲的錯誤，
        免得哪天併發跑起來變成兩個回合互搶訊息這種極難查的症狀。
        """
        if ticket is not None:
            if not ticket.pending():
                raise WakeConsumed()
            q = ticket.q
            self._wake = None
        elif self._inbox is not None:
            if self._wake is None:
                raise RuntimeError(f"對話 {self.conv_id} 已經有回合在收件了")
            q = self._inbox            # 認養尚未被領走的 wake 收件匣
            self._wake = None
        else:
            q = asyncio.Queue()
            self._inbox = q
        try:
            yield _Inbox(q)
        finally:
            self._inbox = None
            self._wake = None
            while not q.empty():
                await self._deliver(q.get_nowait())

    def alive(self) -> bool:
        """讀取者還在跑嗎。掛掉的 mailbox 不能再用，要重建。"""
        return self._task is not None and not self._task.done()

    async def close(self) -> None:
        """停掉讀取者並關閉 client。永不拋例外。"""
        t, self._task = self._task, None
        if t is not None and not t.done():
            t.cancel()
            with contextlib.suppress(BaseException):
                await t
        with contextlib.suppress(Exception):
            await self.client.disconnect()
