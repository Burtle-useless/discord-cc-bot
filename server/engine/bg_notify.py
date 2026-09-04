"""背景工作的帳本，以及它跑完之後的處置。

這個模組同時是 `mailbox` 的回合外處理器，與「哪些背景工作還在跑」的唯一真相。
兩件事寫在一起是因為它們是同一份帳的兩面：開工時記一筆、完成時結一筆，
而「完成」正好就是要推播的那一刻。

**為什麼帳要記在這裡而不是回合裡。** 原本 `runner` 有一個區域變數 `pending_bg`，
回合一結束就跟著消失。於是丟到背景的工作只有在**開它的那個回合還沒結束時**
才看得見——助理說完「等它完成」收工，狀態列上那行「背景：…」就沒了，畫面回到
全空，而工作其實還在跑。下一個回合開始時 `pending_bg` 又是空的，那件事再也
不會出現。帳提升到對話層之後，回合起落不再影響它。

**帳記的是物件不是字串。** 2026-08-25 之前這裡存的是 `{task_id: 描述文字}`，
一句話而已——沒有起始時間、沒有狀態、沒有進度，完成就 `pop` 掉，跑完的事
從世界上消失。而 SDK 一直在送 `TaskProgressMessage`，裡面有 `usage`
（token 數、工具呼叫次數）與 `last_tool_name`，全庫沒有任何一處接它。
使用者看到的因此只有一行「背景：某某某」，看不出跑多久、在做什麼、還要多久。
現在每筆是一個 `BgTask`，該有的都在，完成後也留著（見 `_DONE_KEEP`）。

**完成時做兩件事**：推播（App 沒開在前面時看得到）、結帳（卡片轉成完成樣式）。
「把結果交回給助理讓他接著做」**不在這裡做，而且不該由 butler 做**：CLI 收到
背景工作完成後會自己把 `<task-notification>` 注入、另起一個模型週期讓助理讀結果
接著講——跟官方終端機一模一樣。2026-08-23 到 2026-09-02 之間這裡曾經自己
排一則中文散文（「〔背景工作回報〕…」）進使用者佇列把助理叫醒，而 CLI 原生那個
週期落在回合外被整段丟掉：同一件事模型跑兩遍、使用者看到的是第二遍，畫面上
還多一則他沒打過的話（2026-08-25 回報「甚至我還看得到」；2026-09-02 定案
「走訊息通知太潦草了」）。現在原生那個週期由 `mailbox` 開成 wake 回合接住
（見 `on_wake`），這裡只留「助理為什麼醒來」的線索給 wake 回合開頭用（`wake_reason`）。

SDK 早就把該給的都給了：`TaskStartedMessage` 帶描述、`TaskProgressMessage`
帶進度、`TaskNotificationMessage` 帶狀態與輸出檔路徑，`stop_task()` 還能停。
butler 先前只撿了頭尾兩則的一部分。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    TaskNotificationMessage,
    TaskUpdatedMessage,
)

from protocol import Event, Frontend, make_event

from . import diag
from .mailbox import WakeTicket

log = logging.getLogger(__name__)

# 摘要進推播內文前的長度上限。通知欄本來就只展得開兩三行，
# 更長的內容在 `output_file` 裡，使用者要看細節得叫助理讀那個檔。
_BODY_MAX = 160

# 卡片上要顯示的摘要長度。比推播寬一點（手機的卡片展得開三四行），
# 但遠短於全文——人看的是「跑完了、大概是什麼結果」，不是全文。
_CARD_SUMMARY_MAX = 300

# 三種收場各自的說法。CLI 的 status 只有這三個值（SDK 的 TaskNotificationStatus）。
_TITLES: dict[str, str] = {
    "completed": "背景工作完成",
    "failed": "背景工作失敗",
    "stopped": "背景工作被中止",
}


@dataclass
class BgTask:
    """一件背景工作的當下全貌。

    `description` 只出現在**開工**那則訊息（`TaskStartedMessage`）上，完成通知
    沒有這個欄位。而這兩則訊息中間隔著整個回合——開工在回合內、完成多半在回合外。
    少了它，通知只會是「背景工作完成」加一段摘要，看不出是哪件事。

    `started_at` 存的是起始時刻而不是已經跑了幾秒：秒數由畫面自己推算，跟狀態列
    的計時同一套做法。存秒數的話沒有新事件進來時畫面就停在最後一個數字上，
    看起來像卡住了。
    """

    task_id: str
    description: str
    started_at: float
    status: str = "running"          # running / completed / failed / stopped
    last_tool: str = ""
    tokens: int = 0
    tool_uses: int = 0
    finished_at: float = 0.0
    summary: str = ""
    output_file: str = ""

    def to_wire(self) -> dict[str, Any]:
        """給畫面用的形狀。欄位名縮短是因為狀態心跳兩秒送一次。"""
        return {
            "id": self.task_id,
            "desc": self.description or "背景工作",
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_tool": self.last_tool,
            "tokens": self.tokens,
            "tool_uses": self.tool_uses,
            "summary": self.summary[:_CARD_SUMMARY_MAX],
        }


# 對話 → {task_id: 這件工作的狀態}。
_tasks: dict[str, dict[str, BgTask]] = {}

# 每個對話最多記幾筆。會留下來的除了已完成的紀錄，還有「開了但永遠不會回報完成」
# 的那種（例如啟動常駐服務）。設上限純粹是不讓長命的對話無限長大；
# 滿了就丟最舊的，那筆多半正是永遠不會回來的那種。
_TASKS_MAX = 50

# 完成的工作留幾筆。留著是為了讓人回頭看得到結果——先前完成即刪，
# 使用者一離開畫面那件事就再也找不回來。使用者下次發言時清空（見 clear_done）：
# 你看到了、你回話了，紀錄就該收起來，不必一直佔著畫面。
_DONE_KEEP = 5

# 對話 → 最近一件結束的工作與結束時刻。wake 回合開頭要說得出「助理為什麼醒來」，
# 而通知與 CLI 自己開的那個週期是兩則獨立的訊息（實測相隔 0.8 秒），
# 只能靠這裡把兩者對上。
_recent_done: dict[str, tuple[BgTask, float]] = {}

# 通知之後多久以內開始的 wake 回合算是「為了這件工作醒來」。實測 0.8 秒，
# 抓寬是給 CLI 忙別的事（hook、壓縮）時的餘裕；超過就當成不知道原因的自主週期，
# 畫面上只說「助理接著說」，不硬湊一個過時的理由。
WAKE_REASON_WINDOW = 20.0

# wake 回合的派工函式：(對話 id, frontend, 票)。
#
# 為什麼是註冊而不是直接 import：派工要走 transport 那條 per-conversation 佇列
# （跟使用者發言同一條 worker，回合才不會併行），而 transport 已經 import 了
# engine。engine 反過來 import transport 就是循環依賴。由 transport 在啟動時
# 把自己的派工函式交進來，engine 這一側只認得這個型別。
Waker = Callable[[str, Frontend, WakeTicket], None]
_waker: Waker | None = None


def set_waker(fn: Waker | None) -> None:
    """登記 wake 回合的派工函式。由 transport 層在啟動時呼叫。"""
    global _waker
    _waker = fn


def on_wake(conv_id: str, frontend: Frontend, ticket: WakeTicket) -> bool:
    """`Mailbox` 的 on_wake：模型在回合外自己開口了，把票交給 transport 排隊。

    回傳 False＝沒人接（沒掛 transport，例如單元測試），mailbox 會把那則訊息
    退回 idle 處理，收件匣不會開了沒人讀。
    """
    if _waker is None:
        return False
    _waker(conv_id, frontend, ticket)
    return True


def _trim(book: dict[str, BgTask]) -> None:
    """把超額的紀錄丟掉：先丟最舊的完成品，還是太多才動到進行中的。

    順序是刻意的。進行中的那幾筆是「等一下還會有東西冒出來」的唯一預告，
    丟掉就等於工作完成時沒人說得出是哪件事。
    """
    done = [k for k, t in book.items() if t.status != "running"]
    for k in done[:max(0, len(done) - _DONE_KEEP)]:
        del book[k]
    while len(book) > _TASKS_MAX:
        del book[next(iter(book))]      # dict 保序（3.7+），最舊的排在最前面


def remember(conv_id: str, task_id: str, description: str) -> None:
    """記一筆背景工作。由 `runner` 在收到 TaskStartedMessage 時呼叫。"""
    if not task_id:
        return
    book = _tasks.setdefault(conv_id, {})
    book[task_id] = BgTask(
        task_id=task_id,
        description=(description or "").strip(),
        started_at=time.time(),
    )
    _trim(book)


def progress(
    conv_id: str,
    task_id: str,
    usage: dict[str, Any] | None,
    last_tool: str,
) -> bool:
    """更新一筆進度。由 `runner` 在收到 TaskProgressMessage 時呼叫。

    回傳「畫面上的數字有沒有變」——沒變就不必發事件。CLI 的進度訊息來得比
    人眼需要的頻繁，每則都推一次 `bg.state` 只是把手機的事件流灌滿。
    """
    t = _tasks.get(conv_id, {}).get(task_id)
    if t is None or t.status != "running":
        return False
    before = (t.tokens, t.tool_uses, t.last_tool)
    if usage:
        t.tokens = int(usage.get("total_tokens") or 0)
        t.tool_uses = int(usage.get("tool_uses") or 0)
    if last_tool:
        t.last_tool = last_tool
    return (t.tokens, t.tool_uses, t.last_tool) != before


def _norm_status(status: str) -> str:
    """把兩套結束詞彙收斂成畫面用的那一套。

    終結狀態有兩種來源，用字不一樣：`task_notification` 送 `stopped`，
    `task_updated` 送原始的 `killed`（SDK 的 `TaskUpdatedStatus` 註解寫明
    「CLI maps that to stopped only when it emits a task_notification」）。
    先前這裡是 `status if status in _TITLES else "completed"`，於是 `killed`
    掉進 else 被標成**完成**——被停掉的工作在畫面上顯示成跑完了。
    """
    if status == "killed":
        return "stopped"
    return status if status in _TITLES else "completed"


def finish(
    conv_id: str,
    task_id: str,
    status: str,
    summary: str = "",
    output_file: str = "",
) -> BgTask | None:
    """結一筆帳：標記收場，但**不刪除**。回傳那筆工作，沒記到就回 None。

    不刪是這次翻修的重點。先前這裡是 `pop`，跑完的工作連同它的摘要與輸出檔
    路徑一起從帳上消失，畫面上那行小字也跟著不見——使用者除了一則會被滑掉的
    推播之外，沒有任何地方查得到剛剛那件事的結果。

    結掉的同時記成「最近一件結束的」（`_recent_done`），wake 回合開頭靠它
    說出助理是為了哪件事醒來。回合內結掉的也記：CLI 在回合進行中一樣會把通知
    注入、模型當步就看到，那不會另開 wake 回合，但記了沒有壞處。
    """
    t = _tasks.get(conv_id, {}).get(task_id)
    if t is None:
        return None
    t.status = _norm_status(status)
    t.finished_at = time.time()
    t.summary = (summary or "").strip()
    t.output_file = output_file or ""
    _trim(_tasks[conv_id])
    _recent_done[conv_id] = (t, t.finished_at)
    return t


def wake_reason(conv_id: str) -> dict[str, Any] | None:
    """wake 回合開頭要講的理由：最近剛結束的那件背景工作，超過時間窗就是 None。

    形狀跟卡片同一套（`BgTask.to_wire`），畫面上「背景工作完成」那張卡與
    回合開頭那行「助理為了它醒來」講的是同一件事，用同一份資料才不會兩邊對不上。
    """
    hit = _recent_done.get(conv_id)
    if hit is None:
        return None
    task, at = hit
    if time.time() - at > WAKE_REASON_WINDOW:
        return None
    return task.to_wire()


def orphan_all(conv_id: str) -> int:
    """連線沒了，把還掛在帳上的背景工作全部結掉。回傳結掉幾件。

    **背景工作跑在 CLI 進程裡，進程一被回收就跟著死，而且不會有任何通知**——
    TaskStarted 之後再也收不到 progress，更不會有終結訊息，於是那筆帳永遠停在
    running，卡片就一直掛在畫面上。

    2026-09-01 實例：一件「看五路實際讀值」掛了 12.6 小時，工具 0、token 0。
    `client_pool.peek()` 的註解早就寫著「進程如果已經被回收，工作也早就跟著
    沒了」，但只有使用者按下「停」那條路徑會走到善後，閒置回收自己不做。

    標成 stopped 而不是 failed：它不是跑壞了，是被我們這邊收掉的。
    """
    tasks = active(conv_id)
    for t in tasks:
        finish(conv_id, t.task_id, "stopped",
               "連線被回收，這件工作跟著結束了，結果沒有留下來。")
    # 被我們收掉的不算「助理該為它醒來」的理由——進程都沒了，不會有 wake 回合，
    # 留著只會讓下一個不相干的自主週期頂著一個過時的理由。
    _recent_done.pop(conv_id, None)
    return len(tasks)


def active(conv_id: str) -> list[BgTask]:
    """這個對話還有哪些背景工作在跑。"""
    return [t for t in _tasks.get(conv_id, {}).values() if t.status == "running"]


def wire(conv_id: str) -> list[dict[str, Any]]:
    """畫面要的那份清單：進行中的，加上還沒被收起來的完成紀錄。"""
    return [t.to_wire() for t in _tasks.get(conv_id, {}).values()]


def clear_done(conv_id: str) -> bool:
    """把完成的紀錄收起來，回傳有沒有真的清掉東西。

    時機是使用者發言——他已經看過結果、已經接著講下一件事了，那幾張完成卡片
    再留在畫面上只是擋路。進行中的不動。
    """
    book = _tasks.get(conv_id)
    if not book:
        return False
    gone = [k for k, t in book.items() if t.status != "running"]
    for k in gone:
        del book[k]
    return bool(gone)


def bg_event(conv_id: str) -> Event:
    """一則「目前的背景工作長什麼樣」的事件。

    回合進行中這件事由兩秒一次的狀態心跳帶著走（`status.bg`），但心跳隨回合
    結束而停。回合結束後與回合外的變化——最主要就是工作跑完——得靠這則事件
    才傳得出去。
    """
    return make_event(conv_id, "-", "bg.state", tasks=wire(conv_id))


def terminal_status(msg: Any) -> str | None:
    """這則訊息代表某件背景工作結束了嗎？是的話回它的狀態，否則 None。

    **終結狀態可能只出現在 `TaskUpdatedMessage` 裡。** SDK 的 docstring 寫得
    很直接：「a background task's terminal state can arrive *only* as a
    TaskUpdatedMessage with no accompanying TaskNotificationMessage」，並要求
    追蹤中的 task id 要在**任一種**訊息帶終結狀態時清掉。

    2026-08-29 就踩到了：一件「產生測試件並出圖」的背景工作跑完之後卡片一直停在
    進行中，帳上那筆的進度全是 0。butler 當時只認 TaskNotificationMessage，
    整個專案沒有一處碰過 TaskUpdatedMessage。
    """
    if isinstance(msg, TaskNotificationMessage):
        return msg.status or "completed"
    if isinstance(msg, TaskUpdatedMessage):
        st = msg.status or (msg.patch or {}).get("status")
        return st if st in TERMINAL_TASK_STATUSES else None
    return None


async def handle(conv_id: str, frontend: Frontend, msg: Any) -> None:
    """處理一則回合外訊息：只認背景工作的終結，其餘留一行診斷。

    模型活動（串流事件、AssistantMessage）**不會走到這裡**——`mailbox` 在
    投遞時就把它們開成 wake 回合了。真的落到這裡的模型活動只有一種情況：
    沒掛 transport（測試），那時跟其他型別一樣只記一筆。
    """
    raw = terminal_status(msg)
    if raw is None:
        # 留一行就好。這是「回合外到底會來什麼」的唯一觀測點——沒有它，
        # 將來要判斷該不該處理別的型別時又只能靠猜。
        diag.record("idle_msg", conv=conv_id, msg_type=type(msg).__name__)
        return

    # **一定要正規化**：底下的文案是比對 stopped，而 task_updated 送的是原始的
    # killed。不轉的話被停掉的工作會被說成「跑完了」。
    status = _norm_status(raw)
    # **task_updated 沒有 summary 也沒有 output_file**，那兩個欄位只有
    # notification 帶得出來。這裡一次取好，底下一律用這兩個區域變數——
    # 直接寫 msg.output_file 的話，每多一個使用點就多一次 AttributeError 的機會
    summary = (getattr(msg, "summary", "") or "").strip()
    out_file = getattr(msg, "output_file", "") or ""
    # 同一件工作的終結會來**兩則**：先 task_updated（只有狀態），0 毫秒後
    # task_notification（帶摘要與輸出檔）。兩則都結帳——後到的那則補上摘要——
    # 但推播只發一次，不然手機每件工作都收到兩則一樣標題的通知
    # （2026-09-02 端對端實測抓到的）。
    prev = _tasks.get(conv_id, {}).get(msg.task_id)
    already_done = prev is not None and prev.status != "running"
    task = finish(conv_id, msg.task_id, status, summary, out_file)
    if already_done:
        await frontend.emit(bg_event(conv_id))
        return
    what = task.description if task else ""
    # 標題帶上在做什麼，內文放結果。兩者都可能是空的：描述漏記時退回純標題，
    # 摘要空白時至少說一句話而不是推一則沒有內文的通知。
    title = _TITLES.get(status, "背景工作有結果了")
    if what:
        title = f"{title}：{what}"
    if status == "stopped":
        body = summary or "已經停下來了。"
    else:
        # 跑完（或跑壞）之後 CLI 會立刻讓助理醒來讀結果，wake 回合收工時
        # 會再推一則帶著他結論的通知；這一則只要說「跑完了」。
        body = summary or "跑完了，助理正在看結果。"

    diag.record(
        "bg_done", conv=conv_id, task=msg.task_id, status=status,
        what=diag.head(what, 40), chars=len(summary), out=out_file,
    )
    await frontend.emit(make_event(
        conv_id, "-", "notify",
        title=title[:_BODY_MAX],
        body=body[:_BODY_MAX],
        # 給手機端備用：完整結果在這個檔裡，內容可能有幾百 KB，不塞進事件。
        task_id=msg.task_id,
        output_file=out_file,
        status=status,
    ))
    # 帳結了，卡片要跟著轉成完成樣式
    await frontend.emit(bg_event(conv_id))
