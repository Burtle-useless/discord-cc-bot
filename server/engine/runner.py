"""一個回合的訊息迴圈。

與 cc-bot 的 run_claude 對照，這裡做了一件大事：**把渲染整包拿掉**。
原版為了塞進 Discord 單則 2000 字上限而生的 _animate／_roll_message／
_status_block／_append_trace_line 全部不存在（約 100 行最難維護的程式碼消失），
改成往 frontend 發語意事件，由手機端決定怎麼畫。

控制流本身則逐條保留——那些是踩過坑換來的。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLIJSONDecodeError,
    ProcessError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    TaskProgressMessage,
    TaskStartedMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

import config
from protocol import COALESCABLE, Frontend, make_event

from . import bg_notify, client_pool, diag, models, usage
from .errors import CCError, classify, parse_resets_at
from .mailbox import MailboxClosed, WakeConsumed, WakeTicket
from .fold import (
    NO_RESPONSE,
    clean_reply,
    fold_messages,
    has_done,
    has_wait,
    parse_ask_marker,
    think_digest,
)
from .state import ConvState, eff_effort, eff_model, persist
from .toolinfo import tool_info


def _keep_sid(sid: str | None, state: ConvState) -> None:
    """回合還在跑就先把 session_id 記下來。

    先前唯一的落地時機是回合正常收尾——`fold_messages` 只從 `ResultMessage`
    取 session_id，而那是最後一則訊息。跑到一半被中斷（伺服器重啟、當掉、
    斷電）就完全沒留下任何線索，那條對話從此失聯：狀態檔裡沒有它，對話列表
    自然也不會有它，使用者看到的是「紀錄整條消失」。

    逐字稿其實一直好端端躺在 `~/.claude/projects` 底下，丟的只是「哪條對話
    對到哪個 session」這個對應。使用者 2026-08-23 按了重新啟動，一條正在讀
    PDF 的對話跑了七分鐘、8.8MB 的內容就是這樣不見的。

    第一則 `AssistantMessage` 就帶著 session_id，通常在送出後幾秒內到——
    早得足以救下任何一個長回合。
    """
    if not sid or state.session_id == sid:
        return
    state.session_id = sid
    persist(state)


@dataclass(slots=True)
class TurnResult:
    """一個回合的產出。

    cc-bot 把這些散在四個 by-channel 全域 dict（_last_turn_used_tool / _done /
    _wait / _think），呼叫端再去讀。這裡收成一個回傳值——回合管線需要它們做
    續跑與兜底判定，用回傳值傳遞才不會被跨回合覆寫（那正是 cc-bot 兜底層
    變成死碼的根因）。
    """

    reply: str
    session_id: str | None = None
    ask: dict | None = None
    used_tool: bool = False
    done: bool = False          # CC 打了 [[DONE]]：任務已完成，不要續跑
    wait: bool = False          # CC 打了 [[WAIT]]：正在等使用者回答，不要續跑
    all_think: str = ""         # 本回合全部思考文字（空回覆時的兜底素材）
    stop_reason: str | None = None
    compacted: bool = False     # 這一輪中途被 CLI 的 auto-compact 切過（見 turn.py 的核對）
    ctx_authoritative: bool = False   # state.ctx_tokens 是向 CLI 問到的權威值，不是估算


# 一個回合裡最多跳過幾個「不屬於本 prompt」的 ResultMessage。設上限是避免串流出狀況
# 時無限等下去，屆時寧可退回舊行為（當成本回合的結果）交給既有的空回覆重試接手。
#
# 收發中樞上線後這道防線大部分時候不會被用到——回合外的訊息在回合開始前就被
# 領走了，不會再排在我們的 prompt 前面。留著是因為它擋的不只是那一種：CLI 仍可能
# 在回合**進行中**為系統事件另起一輪。判準本身沒有成本，拿掉只是把餘裕還回去。
MAX_ALIEN_TURNS = 3

# step.commit 帶的文字上限。一步的回覆極少超過幾 KB；設上限只是擋住把整份檔案
# 內容當回覆吐出來那種病態情況，不是為了省事件大小——前端要靠這份文字定稿。
STEP_TEXT_MAX = 20_000

# 讀取迴圈裡只有這幾種例外代表「這條連線已經不能用了」，才值得把 client 丟掉
# （丟掉＝殺進程＝殺掉跑在裡面的背景工作）。其他例外——_commit_step、tool_info、
# bg_notify.progress 裡的小 bug——照樣往上拋讓回合以錯誤收場，但進程好端端的，
# 不該為了一個顯示層的錯誤把跑到一半的背景工作全結掉。
TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    CLIConnectionError, ProcessError, CLIJSONDecodeError, MailboxClosed,
)

# 閒置逾時的輪詢間隔（秒）。抽成模組常數是讓測試把它縮短，不必真的等 5 秒。
IDLE_POLL_SEC = 5.0

# ── 插話（mid-turn steering）──────────────────────────────────────────────
# 回合進行中把新訊息直接 query() 進 CLI stdin，CLI 會在模型下一步決策前注入——
# 跟官方終端機邊跑邊打字同一條路。實測（bundled CLI 2.1.247，2026-09-02）：
#   - 回合進行中送入 → 模型當步轉向，單一 ResultMessage 內完成（不另開回合）；
#   - 撲空（訊息落在 ResultMessage 之後）→ CLI 自行另開一個回應週期處理它。
# 撲空的那個週期沒人讀就會殘留在串流裡毒化下一回合（問 A 回 B），所以插過話的
# 回合收尾時要開觀察窗把孤兒週期讀回來（見 run_turn 讀取迴圈的 orphan_watch）。
_steerable: dict[str, Any] = {}   # conv_id → 進行中回合的 client（讀取階段才登記）
_steered: dict[str, int] = {}     # conv_id → 本回合已插話則數（決定收尾開幾輪觀察窗）
STEER_DRAIN_WINDOW = 6.0          # 秒：自己的 Result 之後等孤兒週期冒頭的時間
                                  #（實測孤兒週期首個事件約 2 秒內就到）
STEER_PREFIX = (
    "〔插話｜使用者在你工作途中送達的新訊息，請立即納入當前工作考量；"
    "與正在做的事衝突時，以這則為準〕\n"
)


async def try_steer(conv_id: str, text: str) -> bool:
    """把訊息注入進行中的回合。沒有可插話的回合就回 False，呼叫端改走排隊。

    **先記帳再送**：query() 寫出去的瞬間回合可能剛好結束（撲空），這筆帳讓
    讀取迴圈在自己的 ResultMessage 之後知道要開觀察窗；帳記在送出之後的話，
    撲空的那次可能趕不上迴圈讀到 Result 的時刻，孤兒週期就沒人接了。
    """
    client = _steerable.get(conv_id)
    if client is None:
        return False
    _steered[conv_id] = _steered.get(conv_id, 0) + 1
    try:
        await client.query(STEER_PREFIX + text)
    except Exception:
        _steered[conv_id] = max(0, _steered.get(conv_id, 1) - 1)
        return False
    return True


class _DeltaBuffer:
    """逐字 delta 的合併緩衝。

    delta 產生速率遠高於手機消化速率，逐則送會在弱網下把前端淹掉，
    ring buffer 也會被灌爆導致續傳視窗變得極短。這裡按時間窗合併後再送。

    時間窗一定要自己顧。先前 `DELTA_COALESCE_MS` 定義了卻沒有任何地方讀，
    flush 只掛在每 2 秒一次的狀態心跳上——逐字串流因此是**兩秒一大塊**地跳出來，
    不是打字機而是幻燈片。設定是死的，症狀卻只看得出「串流有點頓」，
    對著 config 檢查也不會發現，因為那個數字看起來完全正常。
    """

    def __init__(self, frontend: Frontend, conv_id: str, turn_id: str) -> None:
        self._fe = frontend
        self._conv, self._turn = conv_id, turn_id
        # 型別清單取自 COALESCABLE，不要在這裡另寫一份。那個常數原本零使用端，
        # 而它旁邊的註解寫著「這幾類事件量大且可合併」——看起來是它在決定，
        # 實際上決定權在這一行，改常數不會有任何效果。
        self._buf: dict[str, list[str]] = {k: [] for k in COALESCABLE}
        self._last = time.monotonic()
        self._window = config.DELTA_COALESCE_MS / 1000.0

    def add(self, type_: str, d: str) -> None:
        self._buf[type_].append(d)

    async def maybe_flush(self) -> None:
        """距上次送出超過合併窗才真的送。每個 delta 之後呼叫，成本只是一次減法。"""
        if time.monotonic() - self._last >= self._window:
            await self.flush()

    async def flush(self) -> None:
        self._last = time.monotonic()
        for type_, parts in self._buf.items():
            if not parts:
                continue
            joined = "".join(parts)
            parts.clear()
            await self._fe.emit(make_event(self._conv, self._turn, type_, d=joined))


async def _interrupt_then_wait(client: Any, client_task: asyncio.Task) -> None:
    """逾時的收尾第一步：interrupt() 並等 INTERRUPT_GRACE_SEC 看讀取任務會不會自己結束。

    不管結果如何都不拋：拋不拋、要不要 drop，由呼叫端看 client_task 有沒有 done 決定。
    讀取任務若在寬限期內以例外結束，這裡把例外取走（回合反正要以逾時收場），
    免得留一個「exception was never retrieved」。
    """
    if client is not None and hasattr(client, "interrupt"):
        try:
            await client.interrupt()
        except Exception:
            return          # 連 interrupt 都送不出去＝連線早就壞了，直接走 drop
    await asyncio.wait({client_task}, timeout=config.INTERRUPT_GRACE_SEC)
    if client_task.done() and not client_task.cancelled():
        client_task.exception()


async def run_turn(
    prompt: str,
    state: ConvState,
    frontend: Frontend,
    turn_id: str,
    expect_assistant: bool = True,
    wake: WakeTicket | None = None,
) -> TurnResult | None:
    """跑完一個回合。呼叫端負責重試、續跑與錯誤呈現（見 turn.py）。

    [expect_assistant] 為 False 時關掉孤兒輪次過濾，給「整輪由 CLI 自己處理、
    模型不發言」的 prompt 用（唯一實例是 /compact）。

    [wake] 有值是 **wake 回合**：CLI 自己開了一個模型週期（背景工作跑完後它會
    注入 `<task-notification>` 讓模型讀結果接著講），`mailbox` 已經幫它開好
    收件匣、發了這張票。這種回合**不送 prompt**，只接手那個收件匣把週期讀完，
    其餘（串流、定稿、工具事件、記帳）跟一般回合一模一樣——畫面上就是助理自己
    醒來把話講完，跟官方終端機同一個樣子。票已失效（被使用者回合認養走了、
    或連線沒了）就回 None，什麼都不發。
    """
    conv = state.conv_id
    if wake is not None:
        # 孤兒判準要關掉：那條防線擋的是「排在我們 prompt 前面的別人週期」，
        # 而 wake 回合要接的正是那個週期，它的 Result 就是我們的 Result。
        expect_assistant = False
        if not wake.pending():
            return None
    start = time.time()
    ctx_before = state.ctx_tokens     # 用來認出 CLI 中途壓縮過（見下面的驟降判斷）
    ctx_usage: dict[str, Any] = {}    # 回合結束時向 SDK 問到的權威 context 數字
    messages: list[Any] = []
    tool_count = 0
    pending_question: dict = {}
    live_text = ""            # 生成中累積的回應文字
    live_think = ""           # 本步累積的思考（定稿進事件後清空）
    all_think = ""            # 本回合全部思考（不隨每步清空，兜底用）
    compacted = [False]
    consumed = [False]        # wake 票在接手前失效（見 _run_client）
    limit_hit = [False]       # 本回合收到 RateLimitEvent(status=rejected)
    limit_reset: list[float | None] = [None]   # 額度何時回復（CLI 給的 epoch 秒）
    failure: list[CCError | None] = [None]     # 回合以錯誤收場（額度／is_error），在 drop 路徑之外拋
    saw_assistant = [False]   # 本回合有沒有出現過 AssistantMessage（孤兒輪次判準）
    alien_turns = [0]         # 已跳過幾個別人的 ResultMessage
    last_activity = [time.time()]
    # 送出去、還沒收到結果的工具呼叫（tool_use_id）。非空＝有工具正在跑，閒置上限
    # 改用 TOOL_INACTIVITY_TIMEOUT：切片、大檔下載這種沒輸出的工具跑十分鐘很正常，
    # 先前一律 600 秒就 raise TimeoutError → finally drop → 整個 CLI 連同背景工作陪葬。
    tools_pending: set[str] = set()
    client_ref: list[Any] = [None]   # 逾時時要對它 interrupt()，讀取階段才有值
    # 這一步的回覆裡出現了 [[ASK:]]。prompt 早就要求「打了標記就停下來等回答」，
    # 但那是請求不是保證：實際發生過問完之後自己繼續往下做，選項卡停在半空中，
    # 使用者根本沒發現有東西在等。這裡改成硬擋。
    ask_pending = [False]
    ask_interrupted = [False]        # 只打斷一次，收不掉就交給原本的閒置逾時
    deltas = _DeltaBuffer(frontend, conv, turn_id)

    async def _commit_step(am: Any) -> None:
        """把一個 AssistantMessage 定稿成事件。"""
        nonlocal tool_count, live_text, live_think, pending_question
        content = getattr(am, "content", [])
        if not isinstance(content, list):
            return
        await deltas.flush()
        # 思考摘要定稿：串流中的思考是流動的（下一步一到就被蓋掉），這裡留一份固定紀錄。
        # **不論這步有沒有動工具都要留**——cc-bot 曾把這段寫在 has_tool 判斷之後，
        # 純思考的步驟整步被 return 丟掉，思考就跟著蒸發了。
        # 上限拉到 2000：預設的 220 字太短，使用者回報思考「講到一半就被吞掉」。
        # 截在伺服器端等於**手機永遠拿不回後半段**，而思考裡常有他真正要看的東西
        # （取票代碼、金額、日期）。長度問題交給 App 收摺處理——它預設只露幾行，
        # 想看全文再點開，畫面不會被灌爆。
        digest = think_digest(live_think, 2000) if live_think.strip() else ""
        has_tool = any(isinstance(b, ToolUseBlock) for b in content)
        step_text = "".join(
            (b.text or "") for b in content if hasattr(b, "text")
        ).strip()
        # **每一步有文字就定稿，而且帶完整文字。** 先前只在動了工具那步才帶、還截到
        # 400 字，於是前端得靠「本地串流緩衝」猜哪些字算定稿：沒有 delta 的回覆
        # （額度用盡那句、很短的回答、背景對話）就整則消失，背景對話則被截成
        # 400 字（2026-09-02 兩端審查各抓到一次）。定稿是伺服器的事，前端只管畫。
        # 定稿前要過 clean_reply：控制標記（[[DONE]]／[[WAIT]]／[[ASK:…]]）是給系統讀的，
        # 留著就是洩漏。先前只有最後那則 reply.final 清，step.commit 直接送原文——
        # 於是 [[ASK:問題|選項…]] 整串以文字畫在畫面上，下面才是選項卡
        # （2026-09-04 使用者截圖回報）。
        # 標記要在 clean 之前看：clean_reply 會把它整串剝掉。
        if parse_ask_marker(step_text) is not None:
            ask_pending[0] = True
        step_text = clean_reply(step_text)
        if digest or step_text:
            await frontend.emit(make_event(
                conv, turn_id, "step.commit",
                text=step_text[:STEP_TEXT_MAX],
                think_digest=digest,
            ))
        live_think = ""
        if not has_tool:
            return
        for block in content:
            if isinstance(block, ToolUseBlock):
                name = block.name
                inp = getattr(block, "input", {}) or {}
                if name == "AskUserQuestion":
                    pending_question = inp
                tool_count += 1
                if block.id:
                    tools_pending.add(block.id)
                await frontend.emit(make_event(
                    conv, turn_id, "tool.call", **tool_info(name, inp)
                ))
        live_text = ""

    async def _status_loop() -> None:
        """定期送出狀態心跳。取代 cc-bot 的 _animate——這裡不畫任何東西，
        只報事實；轉圈動畫與計時由手機端本地跑，不必為此往返網路。"""
        while True:
            await asyncio.sleep(2)
            await deltas.flush()
            await frontend.emit(make_event(
                conv, turn_id, "status",
                elapsed=round(time.time() - start, 1),
                model=eff_model(state),
                effort=eff_effort(state),
                ctx_tokens=state.ctx_tokens,
                tools=tool_count,
                # 帳在 bg_notify 那邊，不在這個回合裡：上一個回合丟到背景、
                # 現在還在跑的工作也要顯示，否則畫面會說「什麼都沒在跑」。
                bg=bg_notify.wire(conv),
            ))

    async def _run_client() -> None:
        nonlocal live_text, live_think, all_think
        try:
            if wake is not None:
                # 週期已經在跑、收件匣已經開好：只能接現有的連線，絕不能
                # acquire——指紋變了它會把連線丟掉重建，正在跑的週期跟著死。
                box = client_pool.peek(conv)
                if box is None:
                    raise WakeConsumed()
                claiming = box.claim(ticket=wake)
            else:
                box = await client_pool.acquire(state, frontend)
                claiming = box.claim()
            client = box.client
            client_ref[0] = client
            # 登記成收件人：從這裡到離開 `async with` 為止，這條對話的訊息都歸
            # 這個回合。離開之後進來的（丟到背景的工作跑完那類）就沒有回合可歸，
            # 由收發中樞交給 bg_notify 變成推播、或開成下一個 wake 回合。
            async with claiming as inbox:
                if wake is None:
                    await client.query(prompt)
                # prompt 送出後才開放插話：先開放的話，插話可能寫在 prompt 的
                # 前面，CLI 會把它當成獨立的第一則訊息、自己先跑一輪。
                # wake 回合沒有 prompt，一開始就能插——實測週期進行中 query()
                # 會被插進同一週期，跟一般回合無異。
                _steered.pop(conv, None)
                _steerable[conv] = client
                draining = False   # True＝在插話觀察窗內（帶逾時讀）
                orphan_watch = 0   # 還要為插話開幾輪觀察窗
                while True:
                    try:
                        message = await inbox.get(
                            STEER_DRAIN_WINDOW if draining else None)
                    except asyncio.TimeoutError:
                        break   # 觀察窗靜默：插話已被回合吃掉，沒有孤兒週期
                    except MailboxClosed:
                        break   # 串流正常結束（原本 async for 的自然出口）
                    draining = False
                    last_activity[0] = time.time()   # 有任何訊息＝還活著，重置閒置計時
                    # 逐字串流：累積生成中的思考／回應，供前端即時顯示
                    if isinstance(message, StreamEvent):
                        ev = message.event or {}
                        if ev.get("type") == "content_block_delta":
                            delta = ev.get("delta", {})
                            dt = delta.get("type")
                            if dt == "text_delta":
                                d = delta.get("text", "")
                                live_text += d
                                deltas.add("text.delta", d)
                            elif dt == "thinking_delta":
                                # 欄位名是 thinking 不是 text（需 display="summarized"）
                                d = delta.get("thinking", "")
                                live_think += d
                                all_think += d
                                deltas.add("thinking.delta", d)
                            await deltas.maybe_flush()
                        continue
                    messages.append(message)
                    # 工具結果回來了：這個工具不再算「正在跑」。HookEventMessage
                    # （PreToolUse／PostToolUse）走上面那行 last_activity 就夠了——
                    # 任何訊息都算活動；這裡管的是「上限該用哪一個」。
                    if isinstance(message, UserMessage) and isinstance(message.content, list):
                        for blk in message.content:
                            if isinstance(blk, ToolResultBlock):
                                tools_pending.discard(blk.tool_use_id)
                    # 額度用盡的結構化訊號。CLI 在額度撞頂時會送 status=rejected 的
                    # RateLimitEvent，接著讓模型「回」一句 You've hit your session limit
                    # 並正常收出 ResultMessage——只看文字的話它就是一則普通回覆。
                    # 先前這裡沒有任何處理，那句英文被定稿、被續跑、被推播「做完了」
                    # （2026-09-02 12:23 實錄）。記下來，Result 到的時候改走錯誤路徑。
                    if isinstance(message, RateLimitEvent):
                        info = message.rate_limit_info
                        if info.status == "rejected":
                            limit_hit[0] = True
                            limit_reset[0] = info.resets_at or limit_reset[0]
                        continue
                    # CLI 自己的 auto-compact 偵測**不在這裡**，在迴圈結束後比對 context
                    # 驟降。先前這個位置擋的是 `SystemMessage(subtype="compact_boundary")`，
                    # 而 SDK 根本不送這種訊息：已查遍 _internal/message_parser.py 全檔沒有它，
                    # 只在 _internal/sessions.py 讀 jsonl 的註解裡出現過。也就是說這條防線
                    # 從寫下來的第一天就沒生效過——34 次壓縮只核對到 1 次。
                    # 背景任務記帳：讓狀態心跳帶上，不影響收工判斷。
                    # 帳本身在 bg_notify——它得活得比這個回合久，因為工作多半在
                    # 回合結束**之後**才跑完，而那時要說得出是哪件事完成了。
                    if isinstance(message, TaskStartedMessage):
                        bg_notify.remember(
                            conv, message.task_id, message.description or "",
                        )
                        await frontend.emit(bg_notify.bg_event(conv))
                    elif isinstance(message, TaskProgressMessage):
                        # 進度：token 數、工具次數、剛剛在呼叫哪個工具。這幾個
                        # 數字先前整包丟棄，所以卡片上只有一句描述、看不出還活著。
                        # 兩秒一次的狀態心跳也會帶上同一份，這裡只在數字真的變了
                        # 才另外推一則——回合結束後心跳就停了，那時只剩這條路。
                        if bg_notify.progress(
                            conv, message.task_id,
                            getattr(message, "usage", None),
                            message.last_tool_name or "",
                        ):
                            await frontend.emit(bg_notify.bg_event(conv))
                    elif (bg_st := bg_notify.terminal_status(message)) is not None:
                        # 回合還在跑的時候就結束了：助理自己會看到結果，
                        # 不必推播也不必續跑，結帳讓卡片轉成完成樣式就好。
                        #
                        # **判斷交給 bg_notify.terminal_status，不要自己比對型別。**
                        # 終結狀態可能只出現在 TaskUpdatedMessage 裡而完全沒有
                        # TaskNotificationMessage（SDK 的 docstring 明講），先前
                        # 這裡只認後者，那種工作的卡片就永遠停在「進行中」。
                        # summary 與 output_file 只有 notification 帶得出來。
                        bg_notify.finish(
                            conv, message.task_id, bg_st,
                            getattr(message, "summary", "") or "",
                            getattr(message, "output_file", "") or "",
                        )
                        await frontend.emit(bg_notify.bg_event(conv))
                    if isinstance(message, ResultMessage):
                        # CLI 會為系統事件另起自己的回合，而那一輪的 ResultMessage 會排在
                        # 我們這個 prompt 的前面先到（實例：resume 舊 session 時 CLI 投遞的
                        # 孤兒背景任務通知，它有自己的 promptId、自成一輪）。照單全收就會把
                        # 那一輪的空結果當成本回合的回覆——而**我們的 prompt 其實還在 CLI
                        # 那邊跑**。turn.py 隨即送出空回覆重試或 [[CONTINUE]] 續跑，於是同一
                        # 個 session 上同時有兩條 query 在跑，逐字稿從同一個 parentUuid 長出
                        # 兩支＝session 分叉。重啟 resume 只會接回其中一支，另一支的內容還在
                        # 檔案裡但已不在脈絡裡（2026-08-14 失憶事件的真因，見 助理/待辦事項/TODO.md）。
                        # SDK 無從分辨（ResultMessage 不帶 promptId），只能在這裡判。
                        # 判準：我們送出的 prompt 一定會產生 AssistantMessage——就算是模型只
                        # 思考不吐字的 #50597 空回應，也有一則只含 thinking block 的
                        # AssistantMessage。零 AssistantMessage 的回合必然不是我們的。
                        if (
                            expect_assistant
                            and not saw_assistant[0]
                            and alien_turns[0] < MAX_ALIEN_TURNS
                        ):
                            alien_turns[0] += 1
                            messages.clear()   # 別人回合的訊息不能折進我們的回覆
                            continue
                        # 用量記帳。ResultMessage 是唯一帶 usage 與成本的訊息，
                        # 錯過這一則就永遠拿不回來（SDK 不保留歷史）。
                        # **排在孤兒判準之後**：別人回合的 Result 先前也被記成助理的
                        # 成本，一個回合記兩筆。只記我們自己的。
                        usage.record(
                            # 記帳用實際的模型 id：設定裡存的可能是官方別名
                            # （default／opus[1m]），照別名記會在用量頁多一個假模型
                            models.resolve(eff_model(state)),
                            getattr(message, "usage", None),
                            getattr(message, "total_cost_usd", None),
                        )
                        # 以錯誤收場的回合不定稿。額度用盡（上面記的旗標）或 CLI 自己
                        # 標了 is_error，這一輪的文字都不是助理要說的話。**不在這裡 raise**：
                        # 外層 except 會把 client 丟掉，而額度用盡時進程好端端的，
                        # 丟掉只是多付一次啟動、還把背景工作一起殺了。記下來到迴圈外拋。
                        if limit_hit[0] or message.is_error:
                            text = (message.result or "").strip()
                            kind = "RATE_LIMIT" if limit_hit[0] else classify(text or message.subtype)
                            resets = limit_reset[0]
                            if kind == "RATE_LIMIT" and not resets:
                                # CLI 沒送 RateLimitEvent（或沒帶時刻）時，從那句
                                # 「resets 3:30pm」把時刻撈出來，自動續跑才有東西可等
                                resets = parse_resets_at(text)
                            failure[0] = CCError(kind, text or message.subtype, resets_at=resets)
                            break
                        # 照 SDK 原生語意收工：receive_response() 的定義就是「收到
                        # ResultMessage 即結束本回合」，背景工作完成時 CLI 會另起一輪，
                        # 那不屬於這一回合。cc-bot v1.40.0 反其道留在迴圈裡等 pending_bg
                        # 清空，遇到「啟動常駐服務」這類永遠不發完成通知的工作就無限卡死，
                        # 回覆早生成完卻送不出去，一路卡到逾時後整段丟失。
                        #
                        # 走人不代表沒人接：收發中樞還在讀，那一輪的訊息會落到
                        # bg_notify 變成推播。
                        # 插過話就先別收工：撲空的插話會讓 CLI 另開一個回應週期，
                        # 開觀察窗把那個孤兒週期整段讀回來、折進同一個回覆
                        # （多則插話多開幾輪；已被吃掉的份最後只多付一個靜默窗）。
                        # 進入收尾就不再接受新插話——之後的訊息走排隊。
                        orphan_watch += _steered.pop(conv, 0)
                        if orphan_watch > 0:
                            _steerable.pop(conv, None)
                            orphan_watch -= 1
                            draining = True
                            continue
                        break
                    if isinstance(message, AssistantMessage):
                        saw_assistant[0] = True   # 上面的孤兒判準靠這個旗標，漏設會把自己的回合也跳掉
                        _keep_sid(message.session_id, state)
                        await _commit_step(message)
                # 回合跑完，順手向 CLI 問一次 context 的權威數字。
                # butler 原本靠模型名稱字串比對去猜上限（opus → 1M），而實測
                # rawMaxTokens=200000、autoCompactThreshold=167000——猜錯五倍的結果是
                # 主動壓縮的門檻算成 85 萬，永遠到不了，34 次壓縮全是 CLI 在回合中途硬切。
                #
                # 仍在 claim 之內：這通往返走的是控制通道、不經過訊息串流，但期間
                # CLI 送來的訊息會先進收件匣，等離開時再轉給 bg_notify，不會漏。
                try:
                    ctx_usage.update(await client.get_context_usage() or {})
                except Exception:
                    pass      # 問不到就退回估算。這是錦上添花，不能讓它拖垮整個回合
        except WakeConsumed:
            # 票失效不是故障：那個週期已經有人在讀，連線也好端端的，不能丟。
            consumed[0] = True
        except TRANSPORT_ERRORS:
            # 長駐 client 已損壞（連線斷／進程死／串流壞掉）→ 丟棄，下次重建並 resume 接回。
            # 其他例外不進這裡：進程沒壞，丟掉只是多付一次啟動、還把背景工作殺了
            # （見 TRANSPORT_ERRORS）。
            await client_pool.drop(conv)
            raise

    # 回合開頭要說明「這一輪是誰起的頭」：使用者發言，還是助理自己醒來。
    # wake 回合順便帶上理由（最近剛結束的那件背景工作），畫面上那行
    # 「背景工作『x』完成，助理接手」就是從這裡來的。
    start_data: dict[str, Any] = {
        "prompt": prompt, "origin": "wake" if wake is not None else "user",
    }
    if wake is not None:
        start_data["wake"] = bg_notify.wake_reason(conv) or {}
    await frontend.emit(make_event(conv, turn_id, "turn.start", **start_data))
    status_task = asyncio.create_task(_status_loop())
    client_task = asyncio.create_task(_run_client())
    ok = False
    try:
        # 閒置逾時：不限總時長，只在「連續無輸出」超過門檻才視為卡死，
        # 讓長工作流（只要持續有輸出）能一直跑下去。
        # 用 asyncio.wait 而非 sleep 輪詢：任務一完成立即返回，回覆不多等一個輪詢間隔。
        timed_out = False
        while not client_task.done():
            await asyncio.wait({client_task}, timeout=IDLE_POLL_SEC)
            if client_task.done():
                break
            # **打了 [[ASK:]] 就停在這裡等回答。** 提早收工不是逾時，所以走的是
            # interrupt（CLI 自己收出一則 Result、迴圈正常走完、reply.final 照發，
            # 選項按鈕也跟著出來），不是底下那條 TimeoutError → drop 的路。
            # 收不掉就算了，讓原本的閒置逾時接手，不要為了停住而把回合弄壞。
            if ask_pending[0] and not ask_interrupted[0]:
                ask_interrupted[0] = True
                await _interrupt_then_wait(client_ref[0], client_task)
                continue
            # 有工具在跑就放寬：等一個沒輸出的工具跑完不是卡死
            limit = (config.TOOL_INACTIVITY_TIMEOUT if tools_pending
                     else config.INACTIVITY_TIMEOUT)
            # **有背景工作在跑就完全不算閒置。** `run_in_background` 的 Bash 一送出
            # 就回一個 task id、ToolResult 立刻到手，所以 tools_pending 是空的；
            # 模型接著等那件工作跑完，這段期間一個字都不會輸出。門檻於是退回 600 秒，
            # 一件跑十分鐘以上的背景工作必定讓整個回合被判定卡死中止
            # （2026-09-04 使用者截圖：五件背景工作都 completed，回合卻被中止）。
            # 背景工作的存活由 bg_notify 記帳，它有東西就代表這邊在等、不是死了。
            if bg_notify.active(conv):
                last_activity[0] = time.time()
                continue
            if time.time() - last_activity[0] > limit:
                timed_out = True
                break
        if timed_out:
            # 先請 CLI 自己停（interrupt 會讓它收出一則 Result、迴圈自然走完），
            # 給幾秒收尾；收得了就不必殺進程，背景工作留得住。收不了才交給
            # 下面的 finally 取消＋drop。
            await _interrupt_then_wait(client_ref[0], client_task)
            raise asyncio.TimeoutError()
        await client_task
        ok = True
    finally:
        if not client_task.done():
            client_task.cancel()
            try:
                await client_task
            except BaseException:
                pass
            # 被取消＝逾時或使用者喊停：client 卡在半途，丟棄以免下次接到半截回應
            try:
                await client_pool.drop(conv)
            except BaseException:
                pass
        status_task.cancel()
        try:
            await status_task
        except BaseException:
            pass
        await deltas.flush()
        # 插話登記表跟著回合走：正常收工、逾時、喊停、例外都要撤掉，
        # 否則殘留的登記會把下一則訊息插進一個早就結束的回合。
        _steerable.pop(conv, None)
        _steered.pop(conv, None)

    if consumed[0]:
        # 開頭已經發了 turn.start，收個尾讓畫面不要停在「忙碌中」；
        # 內容由認養那個週期的回合負責，這裡沒有東西可折。
        await frontend.emit(make_event(
            conv, turn_id, "turn.end", ok=False, elapsed=round(time.time() - start, 1),
        ))
        return None

    if failure[0] is not None:
        # 這一行跟正常回合的 diag 同一個 kind，事後統計「多少回合以錯誤收場」
        # 才不必另外撈；failed 欄位就是分類。
        diag.record(
            "turn", conv=conv, turn=turn_id,
            prompt=diag.head(prompt, 40) if wake is None else "〔wake〕",
            msgs=[type(m).__name__ for m in messages],
            failed=failure[0].kind, resets_at=failure[0].resets_at, ok=False,
        )
        await frontend.emit(make_event(
            conv, turn_id, "turn.end", ok=False, elapsed=round(time.time() - start, 1),
        ))
        raise failure[0]

    # 這裡原本有「收工時還有背景工作 → 丟掉 client」。理由是那些訊息沒人讀會殘留在
    # 串流裡，被下一則使用者訊息當成自己的回覆讀走（問 A 卻回上一件事的 B）。
    #
    # 已移除：殘留的前提是「沒人讀」，而收發中樞現在一直在讀，回合外的訊息有明確
    # 歸屬（見 mailbox）。斬斷連線換來的乾淨，代價是背景工作完成通知連同它帶的摘要
    # 與輸出檔路徑一起蒸發——使用者永遠不會知道丟到背景的東西跑完了。不丟連線，
    # session 也不必反覆 resume 接回。
    content, new_sid, ctx = fold_messages(messages)
    if ctx:
        state.ctx_tokens = ctx
    # 權威值蓋過估算。三個數字各有用途：
    #   totalTokens          → 現在用掉多少（比 result 的估算準）
    #   rawMaxTokens         → 真正的上限，App 的用量長條要拿它當分母
    #   autoCompactThreshold → CLI 會自己動手的門檻，我們得搶在它前面
    total = int(ctx_usage.get("totalTokens") or 0)
    if total:
        state.ctx_tokens = total
    if int(ctx_usage.get("rawMaxTokens") or 0):
        state.ctx_max = int(ctx_usage["rawMaxTokens"])
    if int(ctx_usage.get("autoCompactThreshold") or 0):
        state.ctx_threshold = int(ctx_usage["autoCompactThreshold"])
    # CLI 在這一輪中途壓縮過嗎。context 只會往上長，唯一會讓它掉下來的就是壓縮，
    # 所以「顯著變小」是可靠的事後判準（取代那個從來沒生效的 compact_boundary）。
    # 抓 0.6 而不是「只要變小就算」：留點餘裕給估算與權威值換算之間的抖動。
    if ctx_before and total and total < ctx_before * 0.6:
        compacted[0] = True
    # 續跑判定要用「清理前」的原文判斷 CC 有沒有打標記
    raw_for_flags = content or live_text or ""
    reply = clean_reply(content)
    # 上游 #50597（Opus 工具回合後偶爾把回合末則 text 掉成空 thinking、stop=end_turn，
    # 文字有 stream 出來卻沒進最終訊息物件）→ 用串流累積的 live_text 回收。
    if not reply and live_text.strip():
        reply = clean_reply(live_text)
    stop_reason = None
    if not reply:
        for m in reversed(messages):
            sr = getattr(m, "stop_reason", None)
            if sr:
                stop_reason = sr
                break

    result = TurnResult(
        reply=reply or NO_RESPONSE,
        session_id=new_sid,
        # 內建工具優先、文字標記兜底。實際上 pending_question 永遠是空的——
        # AskUserQuestion 不在 SDK 的工具清單裡（見 fold.ASK_RE 的說明），
        # 真正在運作的是後面這條。前者留著是為了工具哪天回來時自動接上。
        ask=pending_question or parse_ask_marker(raw_for_flags),
        used_tool=tool_count > 0,
        done=has_done(raw_for_flags),
        wait=has_wait(raw_for_flags),
        all_think=all_think,
        stop_reason=stop_reason,
        compacted=compacted[0],
        ctx_authoritative=bool(total),
    )
    # 這一行是「訊息被吃掉」唯一查得動的證據。三個長度擺在一起才有意義：
    #   texts   → 模型真的講了幾則、各多長（對得上 CC 逐字稿就代表 runner 讀到了）
    #   live    → 串流累積到多少（有值而 fold 為空＝訊息物件掉字，#50597）
    #   reply   → 最後折出來要送的長度（0 就是這裡斷的，不必再往下查）
    # 沒有它的時候，同一個症狀有三種可能的斷點而事後完全分不出來（見 diag.py）。
    diag.record(
        "turn",
        conv=conv, turn=turn_id,
        prompt=diag.head(prompt, 40) if wake is None else "〔wake〕",
        msgs=[type(m).__name__ for m in messages],
        texts=[
            len("".join(b.text for b in m.content if hasattr(b, "text")))
            for m in messages if isinstance(m, AssistantMessage)
        ],
        live=len(live_text), fold=len(content), reply=len(reply or ""),
        tools=tool_count, alien=alien_turns[0], compacted=compacted[0],
        stop=stop_reason, ok=ok,
    )
    await frontend.emit(make_event(
        conv, turn_id, "turn.end", ok=ok, elapsed=round(time.time() - start, 1),
    ))
    # 狀態心跳隨回合結束而停，而背景工作不會。收工前補一則，畫面上那行
    # 「背景：…」才留得住——不然助理說完「等它完成」，指示就跟著消失了。
    await frontend.emit(bg_notify.bg_event(conv))
    return result
