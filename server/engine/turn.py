"""回合管線：一則使用者訊息的完整生命週期。

這一層是整個引擎「可靠性密度」最高的地方，內容幾乎全部來自 cc-bot 一年份的災情
（`discord_bot.py` 3596-3718 行）。單看每一段都像過度防禦，但每一段都對應一次真實故障：

- 空回覆三層防線 → 上游 #50597：模型只產思考塊、一個字不寫就 end_turn
- 自動續跑 → 任務做到一半停手，使用者看到半成品以為做完了
- auto-compact → context 撐爆後整個對話報廢
- 錯誤善後分流 → 「重新登入也救不回的 401」

`runner.run_turn` 只負責跑一個回合；要不要重試、要不要續跑、怎麼善後，全在這裡。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import config
from protocol import AskChoice, AskRequest, AskResponse, Event, Frontend, make_event

from . import client_pool, diag
from .errors import CCError, wrap
from .fold import NO_RESPONSE, think_digest
from .mailbox import WakeTicket
from .runner import TurnResult, run_turn
from .state import ConvState, eff_model, persist

# context 用到幾成就先壓縮。0.85 是 cc-bot 實測值：再高就有機會在壓縮前先撞爆。
# 只在拿不到權威門檻時才會用到（見 compact_threshold）。
COMPACT_AT = 0.85

# 相對於 CLI 自己的 auto-compact 門檻，我們要提前多少動手。
# 這個係數才是實際生效的那一個。要搶在 CLI 前面的理由：CLI 是在回合**跑到一半**
# 才就地切，工具跑完、話還沒說出口就被截斷；butler 自己壓是在回合**開始前**，
# 使用者看到的是「我先整理一下記憶」然後正常回話。
# 0.9 是刻意留窄的：壓太早等於白白剪掉還能用的脈絡，而 0.9×167000≈150K，
# 距離 CLI 的門檻還有一萬七千 token 的緩衝，夠一個普通回合用。
COMPACT_SAFETY = 0.9

# 狀態列的階段旗標：壓縮期間的每則 status 都帶著它，手機端才知道這段時間
# 它不是在回話，是在整理記憶。空值＝一般回合。
COMPACT_PHASE = "compacting"

# 各模型的 context 上限。抓保守值即可——這只用來決定何時壓縮，估低頂多多壓一次。
CTX_LIMIT_DEFAULT = 200_000
CTX_LIMIT_1M = 1_000_000
# 高階模型在這些方案下原生就是 1M context（cc-bot context_limit_for 的判定）
_BIG_CTX_PLANS = frozenset({"max", "team", "enterprise"})
_BIG_CTX_FAMILIES = ("opus", "fable", "mythos")

# 開頭必須是 CLI 的 /compact 指令。先前這裡是一句普通中文（「請把對話壓縮成
# 重點摘要…」），模型乖乖輸出一篇摘要文字，但 CLI 的 context **完全沒有被剪**；
# ctx_tokens 歸零後下一輪又立刻超標，於是每則訊息前都假壓一次（實錄：三分鐘
# 觸發四次），摘要問答還全數寫進 session 逐字稿被歷史載入原樣顯示。
# /compact 由 CLI 自己處理：真的剪 context、整輪零 AssistantMessage（cc-bot
# discord_bot.py:1435 的實測註解），runner 只等 ResultMessage 所以天然相容。
COMPACT_PROMPT = (
    "/compact 壓縮時務必完整保留：本次對話實際完成的改動"
    "（檔案路徑、新增或修改的函式與指令與欄位名稱）、版本號變動、尚未完成的待辦。"
    "做了什麼比討論了什麼更重要，不可省略。"
)

CONTINUE_NUDGE = (
    "剛才那一步還沒收尾。如果整件事已經完成，只要回覆 [[DONE]] 就好；"
    "如果還在等我回答，回覆 [[WAIT]]；否則請接著把它做完。"
)

EMPTY_RETRY_NUDGE = "剛才沒有收到你的文字回覆，請把結果直接說出來。"

# 回合中途被 CLI 的 auto-compact 切斷後的核對提示。
# 實錄：記帳工具跑完、那句「記好了」還沒說出口就撞上壓縮，重啟後的摘要裡卻寫著
# 「已經跟他說記好了」——工具真的執行了，使用者卻從頭到尾沒看到任何確認。
# 摘要是模型自己寫的，寫錯了沒有任何機制會發現，只能壓縮後強制回頭對一次帳。
COMPACT_RECHECK_NUDGE = (
    "剛才這一輪中途觸發了自動壓縮，你現在看到的前文是摘要而不是原文。"
    "摘要有可能把「還沒做」寫成「做完了」，也可能漏掉你已經做完、但話還沒說出口的事。"
    "請對照使用者最初那則訊息逐項核對：他交代的每一件事，你是真的做完、而且把結果講給他聽了嗎？"
    "有漏掉的現在補上（只補漏掉的那句，不要把已經講過的重講一遍）；"
    "全部都交代過了就只回 [[DONE]]。"
)

# 星期的中文寫法，`_stamp` 用。`weekday()` 是 0=週一。
_WEEKDAYS: tuple[str, ...] = ("一", "二", "三", "四", "五", "六", "日")


def _stamp(text: str, src: str = "") -> str:
    """在使用者訊息前面加上送出時間與來源，格式 `[08/21 週四 11:04 手機] `。

    模型手上唯一的時間資訊是 CLI system prompt 那句「今天是幾月幾號」，而對話裡
    每一則訊息都沒有時間。它看得到前面說過什麼，卻完全不知道那是十分鐘前還是
    三天前——這條對話又是持續的、跨好幾天的，於是它只能猜，猜出來的就是
    「我昨天講錯的一件事」，而那其實是同一天稍早。使用者 2026-08-21 回報
    「他經常會說昨天，但明明只是上一波對話而已」。壓縮過後更嚴重：摘要裡連
    日期痕跡都不剩。

    加星期是因為 system prompt 只給日期不給星期，模型連今天禮拜幾都不知道，
    而「下週三」這種話每天都在講。

    來源（手機／電腦）擠在同一個方括號裡，不另開一個前綴：那是**同一件事的兩個面向**
    ——「他此刻人在哪、拿著什麼在打字」。模型讀到「電腦」就知道長回覆、檔案路徑、
    程式碼區塊他看得到；讀到「手機」就知道畫面只有那麼大。兩個獨立前綴會讓每則訊息
    開頭堆兩串括號，而剝除規則也要維護兩套。

    這個前綴**不會被使用者看到**：手機端即時顯示的是他自己打的原文（App 送出時
    就畫上去了），重建歷史時 `history._STAMP_RE` 會把它剝掉。改格式一定要同步改
    那個正則，不然重開 App 之後每則訊息前面都會多出一串他沒打過的字。
    """
    now = datetime.now()
    tail = f" {src}" if src else ""
    return f"[{now:%m/%d} 週{_WEEKDAYS[now.weekday()]} {now:%H:%M}{tail}] {text}"


def stamp(text: str, src: str = "", attachments: list[dict] | None = None) -> str:
    """`_stamp` 的公開入口。transport 的插話路徑也要蓋章——插進去的訊息跟排隊的
    一樣是人說的話，模型一樣需要知道那是幾點、從哪一端來的。

    附件在這裡才接成路徑清單，跟時間戳同一個道理：**模型看得到、人看不到**。
    先前是 App 自己把「我從手機傳了這些檔案給你：<一串路徑>」拼進訊息本文再送出，
    於是那串路徑變成使用者自己氣泡裡的文字。現在附件是訊息的結構化欄位，
    畫面照欄位畫縮圖與檔案卡，只有送給模型的這一份才把路徑接上去。
    """
    body = _stamp(text, src)
    if not attachments:
        return body
    lines = "\n".join(
        f"- {a.get('path', '')}" for a in attachments if a.get("path")
    )
    if not lines:
        return body
    head = body if text.strip() else body.rstrip()
    return f"{head}\n\n（附件，直接讀檔）\n{lines}"


def ctx_limit(state: ConvState) -> int:
    """這條對話的 context 上限。App 的用量長條拿它當分母。

    **CLI 問到的值優先。** 底下那套靠模型名稱字串比對的判斷是猜的，而且猜錯了：
    `claude-opus-5` 被歸進「大 context 家族」算成 1M，實測 `rawMaxTokens=200000`，
    整整差五倍。後果不只是長條顯示的百分比是實際的 1/5，更嚴重的是主動壓縮的門檻
    跟著算成 85 萬——那個數字永遠到不了，所以 butler 從來沒有自己壓縮過一次。
    （2026-08-17 以 `server/_diag_ctx.py` 問 SDK 的 `get_context_usage()` 確認。）

    猜測邏輯留著當開機後第一個回合的兜底：那時還沒問過 CLI，`ctx_max` 是 0。
    """
    if state.ctx_max:
        return state.ctx_max
    model = (eff_model(state) or "").lower()
    if model.endswith("[1m]"):          # 明確要求 1M 的後綴，優先於方案判斷
        return CTX_LIMIT_1M
    if config.ACCOUNT_PLAN in _BIG_CTX_PLANS and any(f in model for f in _BIG_CTX_FAMILIES):
        return CTX_LIMIT_1M
    return CTX_LIMIT_DEFAULT


def compact_threshold(state: ConvState) -> int:
    """用到多少 token 就該由 butler 主動壓縮。

    有 CLI 給的 auto-compact 門檻就照它算，這是唯一能真正搶在 CLI 前面的做法：
    即使把上限從錯誤的 1M 改回正確的 200K，`200000×0.85=170000` 仍然**晚於**
    CLI 的 167000，主動壓縮照樣不會發生。門檻要對著門檻算，不是對著上限算。
    """
    if state.ctx_threshold:
        return int(state.ctx_threshold * COMPACT_SAFETY)
    return int(ctx_limit(state) * COMPACT_AT)


def parse_ask(ask_data: dict) -> AskRequest | None:
    """把 AskUserQuestion 的工具輸入轉成 AskRequest。

    一次只處理第一題：手機一問一答的介面收不了多題，多的會被吞掉（cc-bot 的防呆）。
    """
    if not isinstance(ask_data, dict):
        return None
    questions = ask_data.get("questions") if "questions" in ask_data else [ask_data]
    if not questions:
        return None
    q = questions[0]
    if not isinstance(q, dict):
        return None
    choices = [
        AskChoice(
            id=str(o.get("label", "")),
            label=str(o.get("label", "")),
            detail=str(o.get("description", "")),
        )
        for o in (q.get("options") or [])
        if isinstance(o, dict) and o.get("label")
    ]
    if not choices:
        return None
    return AskRequest(
        kind="choose",
        title=str(q.get("question") or q.get("header") or "需要你決定一件事"),
        body="",
        choices=choices,
        timeout_sec=config.CONFIRM_TIMEOUT_SEC,
    )


class _QuietFrontend:
    """只讓 status 通過的 Frontend 包裝。

    auto-compact 會叫模型把整段對話寫成重點摘要。那一輪的 text.delta 是**維運
    產物、不是助理在跟使用者說話**，先前卻跟正常回覆走同一條路送到畫面上，
    使用者看到的就是「先打出一大堆對話摘要，然後被下一個 turn.start 清掉」——
    他的原話「顯示一堆對話摘要再收回去」講的就是這個。

    status 仍然放行：壓縮可能跑十幾秒，全靜音會變成畫面卡住不動沒人知道在幹嘛。
    放行時蓋上 phase 旗標——這一輪的思考被擋掉了，手機端的狀態列沒東西可寫就
    退回預設的「想一下」，看起來跟平常在回話一模一樣，人不知道它其實在整理記憶。
    每兩秒一次的心跳都會經過這裡，所以旗標自然覆蓋整段壓縮期間。
    """

    def __init__(self, inner: Frontend) -> None:
        self._inner = inner

    async def emit(self, ev: Event) -> None:
        if ev.type == "status":
            # replace 而不是 make_event：重建會拿到新序號，斷線續傳的錨點會亂
            await self._inner.emit(
                replace(ev, data={**ev.data, "phase": COMPACT_PHASE})
            )

    async def ask(self, req: AskRequest) -> AskResponse | None:
        # 壓縮不該問問題，真問了也要讓它問得到——沉默地吞掉會讓回合卡到逾時
        return await self._inner.ask(req)


# 壓縮失敗後多久內不再試。2026-09-02 12:23 實錄：額度用盡期間每則訊息前都先跑一次
# /compact，壓縮本身也被限流、context 沒降，下一則再壓——一句「？」觸發 compact＋
# 限流回覆各一次。壓縮沒把 context 壓下來就是失敗，失敗就先冷卻。
COMPACT_COOLDOWN_SEC = 600.0
_compact_failed: dict[str, float] = {}     # conv → 上次壓縮失敗的 monotonic 時刻

# 額度用盡時 CLI 給的回復時刻（epoch 秒）。在那之前不要再對這條對話做任何會叫模型
# 的事（壓縮、續跑），transport 也靠它決定排隊的訊息要等到什麼時候自動重跑。
_limit_until: dict[str, float] = {}


def limit_until(conv: str) -> float | None:
    """這條對話的額度什麼時候回復（epoch 秒）；沒有限流或已過期就是 None。"""
    t = _limit_until.get(conv)
    if t is None:
        return None
    if t <= time.time():
        _limit_until.pop(conv, None)
        return None
    return t


async def _maybe_compact(state: ConvState, frontend: Frontend, conv: str) -> None:
    """context 接近上限時先壓縮，避免下一回合直接撞 CONTEXT_FULL。

    壓縮成不成功要看**壓縮後 CLI 回報的 context**，不是壓縮回合有沒有正常結束：
    先前這裡跑完一律 `ctx_tokens = 0`，而 runner 每輪結束都會拿權威值蓋回去，
    沒真的壓到的話下一則訊息前又超標、又壓一次，無限循環。
    """
    if state.ctx_tokens < compact_threshold(state):
        return
    if limit_until(conv) is not None:
        return      # 額度還沒回復，壓縮也只會再撞一次
    failed_at = _compact_failed.get(conv)
    if failed_at is not None and time.monotonic() - failed_at < COMPACT_COOLDOWN_SEC:
        return
    before = state.ctx_tokens
    turn_id = f"c-{uuid.uuid4().hex[:8]}"
    await frontend.emit(make_event(
        conv, turn_id, "status", note="對話有點長了，我先整理一下記憶",
        phase=COMPACT_PHASE,
    ))
    try:
        # expect_assistant=False：/compact 整輪由 CLI 自己處理，實測只有 SystemMessage／
        # UserMessage／ResultMessage，模型完全不發言。套孤兒判準會把它自己的 ResultMessage
        # 當成別人的而一直等，直到閒置逾時。
        res = await run_turn(
            COMPACT_PROMPT, state, _QuietFrontend(frontend), turn_id,
            expect_assistant=False,
        )
    except Exception:
        # 壓縮失敗不該讓使用者的原始需求跟著陣亡，讓它繼續往下跑；但要記住失敗，
        # 不然下一則訊息前又來一次
        _compact_failed[conv] = time.monotonic()
        return
    if res is None:
        return
    if res.session_id:
        state.session_id = res.session_id
    if not res.ctx_authoritative:
        # 問不到權威值時退回舊行為：當它壓成功了，下一回合會問到真的數字
        state.ctx_tokens = 0
    elif state.ctx_tokens >= before * 0.8:
        # 回合正常結束但 context 幾乎沒動＝CLI 沒有真的壓（多半是限流時模型回了
        # 一句話而不是摘要）。冷卻，別讓每一則訊息都白付一次壓縮。
        _compact_failed[conv] = time.monotonic()
    else:
        _compact_failed.pop(conv, None)
    persist(state)


@dataclass
class TurnOutcome:
    """一則使用者訊息從頭到尾跑完的總結，只給收尾的 `turn.done` 用。

    為什麼要在 `handle_turn` 這層累積，而不是直接拿最後一個 `TurnResult`：
    空回覆重試與自動續跑會產生好幾個 `TurnResult`，「這則訊息動過工具嗎」
    是它們的**聯集**。只看最後一輪會漏——最後那輪往往只回一句「做完了」
    而不動任何工具，於是真正做了半小時苦工的訊息反而被判成閒聊、不推播。
    """

    used_tool: bool = False
    """整段期間動過任何工具。閒聊不推播，靠這個分辨。"""

    last_markdown: str = ""
    """最後一則定稿的內容，當推播內文。"""

    pending_ask: bool = False
    """停在提問上收工。手機端據此把推播文案換成「助理在等你回答」。

    **不要**寫成「推播由 ask.request 負責」——`[[ASK:]]` 走的是 inline 那條路，
    選項跟著 `reply.final` 一起送出去，這條路從頭到尾不會發 `ask.request`。
    那個事件現在只剩工具權限確認（`hub.ask`）在用。
    """


async def handle_turn(
    text: str, state: ConvState, frontend: Frontend, src: str = "",
    attachments: list[dict] | None = None,
) -> CCError | None:
    """處理一則使用者訊息，直到產出最終回覆或明確的錯誤。

    `src` 是這則話從哪一端送進來的（「手機」／「電腦」），會蓋進時間戳裡。

    回傳收場的錯誤（已經呈現給使用者了），正常收工回 None。transport 拿它決定
    要不要把這則訊息留著等額度回復再重跑（RATE_LIMIT 且有 resets_at）。
    """
    conv = state.conv_id
    out = TurnOutcome()
    started = time.monotonic()
    try:
        await _maybe_compact(state, frontend, conv)
        # 只有真的使用者訊息蓋時間戳。續跑與重試的提示走別的路徑進來，
        # 那些是同一則訊息的內部往返，蓋上去只會讓歷史多出幾個假的時間點
        await _run_with_recovery(
            stamp(text, src, attachments), state, frontend, conv, out)
    except CCError as e:
        # 出錯有自己的推播（error 事件 →「出狀況了」），不要再補一則「做完了」
        await _handle_error(e, state, frontend, conv)
        return e
    except Exception as e:  # noqa: BLE001 — 最外層守門，任何漏網都要變成事件而非靜默
        err = wrap(e)
        await _handle_error(err, state, frontend, conv)
        return err
    await _turn_done(frontend, conv, out, started)
    return None


async def handle_wake(
    ticket: WakeTicket, state: ConvState, frontend: Frontend,
) -> None:
    """助理自己醒來的那一輪。

    背景工作跑完後 CLI 會自己開一個模型週期：注入 `<task-notification>`、
    讓模型讀結果、接著把話講完——官方終端機裡「助理自己接手」就是這個。
    `mailbox` 在回合外看到那個週期的第一則串流就開好收件匣、發票排進佇列，
    輪到這裡就接手讀完，收尾走跟使用者回合同一套（定稿、選項、壓縮核對、
    未完成續跑）。

    跟 `handle_turn` 的差別只有三處：沒有 prompt（週期已經在跑）；不先壓縮
    （同理）；不做空回覆重試——模型醒來決定不說話就算了，硬逼它講一句只是
    多一輪空話。收工推播也只在它真的說了話時才發：任務結束那一刻 bg_notify
    已經推過「跑完了」，助理什麼都沒說就不必再吵一次。
    """
    conv = state.conv_id
    out = TurnOutcome()
    started = time.monotonic()
    try:
        res = await run_turn(
            "", state, frontend, f"w-{uuid.uuid4().hex[:8]}", wake=ticket,
        )
        if res is None:
            # 票已失效：那個週期由別的回合讀走了，內容不歸這裡。但 runner 可能
            # 已經發過 turn.start，手機端的忙碌旗標只認 turn.done／error 才會清——
            # 不收尾就是狀態列與停止鍵掛到下一次重連。收一則安靜的 turn.done。
            await _turn_done(frontend, conv, out, started, notify=False)
            return
        _persist_sid(res, state)
        await _settle(res, state, frontend, conv, out)
    except CCError as e:
        await _handle_error(e, state, frontend, conv)
        return
    except Exception as e:  # noqa: BLE001 — 同 handle_turn：漏網的也要變成事件
        await _handle_error(wrap(e), state, frontend, conv)
        return
    await _turn_done(frontend, conv, out, started, notify=bool(out.last_markdown))


async def _turn_done(
    frontend: Frontend, conv: str, out: TurnOutcome, started: float,
    notify: bool = True,
) -> None:
    """整則訊息真的收工了。**手機端的「做完了」推播只認這一則。**

    先前推播綁在 `reply.final` 上，而那個事件**每一輪都會發一次**——自動續跑
    每續一輪一則、壓縮核對再一則。使用者收到通知點進來，助理還在跑下一輪，
    畫面上是思考中。他的原話：「通知這個動作應該要在最後才對」。

    `pending_ask` 那種收工照樣發，讓「這則訊息處理完了」這件事只有一個訊號源；
    要不要據此推播、推什麼文案由手機端決定——**這是停在提問上時唯一的推播來源**，
    inline ask 不發 `ask.request`（見 `TurnOutcome.pending_ask`）。

    耗時由伺服器算並且從 `handle_turn` 進來就起算。手機端原本自己拿 turn.start
    當起點，續跑會重設它，量到的是**最後一輪**的長度——一件跑了十分鐘的事
    只要最後一輪快，就會因為不到 NOTIFY_AFTER_SEC 而整個不推播。
    """
    elapsed_ms = int((time.monotonic() - started) * 1000)
    await frontend.emit(make_event(
        conv, "-", "turn.done",
        # 一律推播。這裡原本有「動過工具，或跑超過 60 秒」的門檻，用意是不要為了
        # 閒聊吵人——但它把「值不值得吵」判斷錯了：使用者發訊息給助理就是在等回覆，
        # 問一句路程幾公里、二十秒答完、沒動工具的那種，正好整個被濾掉，
        # 而那恰恰是他人在外面最需要被通知的一類。
        #
        # 真正該決定「要不要出聲」的是**他現在看不看得到畫面**，而那個判斷在手機端
        # 已經有了：App 開著的時候背景服務是停的，這則事件根本沒人收；服務會跑
        # 就代表他在別的地方。所以這裡一律發，讓那一份判斷是唯一一份。
        #
        # 只有使用者送訊息才會走到 handle_turn（排程與提醒走 `notify` 事件那條），
        # 所以不必擔心自主回合把人洗版。唯一的例外是 wake 回合（handle_wake）：
        # 助理醒來卻一個字沒說時關掉，那時該講的 bg_notify 已經講過了。
        notify=notify,
        # 以下三個給診斷與內文用
        used_tool=out.used_tool,
        # 截短是為了不讓一篇長回覆整個塞進事件流
        markdown=out.last_markdown[:200],
        pending_ask=out.pending_ask,
        elapsed_ms=elapsed_ms,
    ))


def ask_dict(req: AskRequest) -> dict:
    """把 AskRequest 轉成放進 `reply.final` 的 `ask` 欄位。

    格式必須跟 `fold.ask_payload`（重建歷史時從原文抽的那條路）**一模一樣**，
    否則同一則訊息在「剛送到」與「重開 App 之後」會長出不同的按鈕。
    tests/test_ask_inline.py 釘住這件事。
    """
    return {
        "title": req.title,
        "choices": [{"id": c.id, "label": c.label, "detail": c.detail}
                    for c in req.choices],
    }


async def _say(
    frontend: Frontend, conv: str, markdown: str, out: TurnOutcome,
    ask: AskRequest | None = None,
) -> None:
    """把一輪的回覆定稿到畫面上。

    `ask` 有值代表這則話後面跟著一組選項。選項是**這則訊息的一部分**跟著送出去，
    不是另外開一個等答案的對話框——所以它不會逾時、不會被收回，重開 App
    也還在（重建歷史時從原文的 [[ASK:]] 標記重新抽，見 fold.ask_payload）。

    先前的做法是伺服器停在 `frontend.ask()` 等答案、五分鐘沒回就放棄並把卡片
    收掉。使用者在手機上常常隔更久才回，回來就看到選項不見了、也不知道剛才
    被問了什麼。2026-08-19 改成現在這樣：助理問完就收工，他什麼時候點都算數。

    `pending_ask` 旗標留著給通知用：手機端靠它判斷這則不是「做完了」而是
    「在等你回答」，兩者的通知文案不一樣。
    """
    # 定稿是「助理說的話」離開伺服器的**唯一**出口，所以這裡一定要留紀錄：
    # 有這一行而使用者看不到 → 問題在 SSE 或 App；沒有這一行 → 話根本沒送出來。
    # 少了它，兩者在事後無從分辨（見 diag.py 的說明）。
    diag.record("say", conv=conv, chars=len(markdown),
                head=diag.head(markdown, 40), pending_ask=ask is not None)
    payload: dict[str, Any] = {
        "markdown": markdown, "pending_ask": ask is not None,
    }
    if ask is not None:
        payload["ask"] = ask_dict(ask)
    await frontend.emit(make_event(conv, "-", "reply.final", **payload))
    # 收工推播的內文取最後一則定稿。記在這裡而不是各個呼叫點，
    # 是因為這裡是定稿的唯一出口，漏不掉任何一條路徑。
    out.last_markdown = markdown


async def _run_with_recovery(
    text: str, state: ConvState, frontend: Frontend, conv: str,
    out: TurnOutcome,
) -> None:
    """跑一則訊息，含空回覆重試、未完成續跑。

    **每一輪拿到回覆就立刻定稿**，不再累積到最後一次送出。

    先前是把所有輪次串成一則、等全部跑完才發 reply.final。但續跑與重試之間會
    夾一個新的 turn.start，前端收到就把已經逐字打在畫面上的上一輪回覆清空——
    使用者眼中就是「打了一大段又收回去」，而且要等所有續跑結束才重新出現。
    改成每輪各自定稿後，串流文字原地變成定稿內容，中間不再有空窗。
    """
    res = await _run_once_with_empty_retry(text, state, frontend, conv)
    await _settle(res, state, frontend, conv, out)


async def _settle(
    res: TurnResult, state: ConvState, frontend: Frontend, conv: str,
    out: TurnOutcome,
) -> None:
    """第一輪跑完之後的收尾：定稿、帶選項、壓縮核對、未完成續跑。

    使用者回合與 wake 回合共用這一段。兩者從這裡開始就沒有差別了——
    模型講完一輪之後該做的事，跟這一輪是誰起的頭無關。
    """
    out.used_tool = out.used_tool or res.used_tool
    said = False        # 這則訊息至少已經回過一句話（決定要不要續跑）

    # 助理想問他一件事 → 選項跟著這則回覆一起送出去，然後這個回合就結束。
    # 這裡刻意**不等答案**：他在手機上，可能十分鐘後才看到、也可能改成自己打字
    # 回答。伺服器空等只有兩種下場——逾時把卡片收掉（他回來就看不到剛才被問
    # 什麼），或是一路占著一個回合。他點選項時就是送一則普通訊息，走新的回合。
    ask_req = parse_ask(res.ask) if res.ask else None
    out.pending_ask = ask_req is not None

    if res.reply and res.reply != NO_RESPONSE:
        # 問題前的說明也走這裡：使用者得先看到「在問什麼」才看得懂選項
        await _say(frontend, conv, res.reply, out, ask=ask_req)
        said = True
    elif ask_req is not None:
        # 只打了標記、正文空白。規則要求選項也要寫進正文，但真的沒寫時
        # 至少要有題目可看，否則畫面上會是幾顆沒頭沒尾的按鈕。
        await _say(frontend, conv, ask_req.title, out, ask=ask_req)
        said = True

    if ask_req is not None:
        # 等他回答，不續跑。壓縮核對也一起跳過：那一輪的用途是確認「工作做到
        # 一半被壓縮切斷」，而停下來問問題本來就沒有未完成的動作要接。
        return

    if res.compacted:
        # 核對輪多半只回一句話、不動工具，直接拿它的 res 去判續跑會把
        # 「原本那輪動過工具卻沒收尾」的事實抹掉——被壓縮切斷的回合正是
        # 最需要續跑的一種，卻剛好因為插了這一輪而永遠不會續跑。
        # 只把 used_tool 併回來：done／wait 要以核對輪為準（它說做完了就是做完了）。
        used_before = res.used_tool
        res = await _recheck_after_compact(state, frontend, conv, out)
        res.used_tool = res.used_tool or used_before
        said = said or bool(res.reply and res.reply != NO_RESPONSE)
        out.used_tool = out.used_tool or res.used_tool
    await _auto_continue(said, res, state, frontend, conv, out)


async def _run_once_with_empty_retry(
    prompt: str, state: ConvState, frontend: Frontend, conv: str,
) -> TurnResult:
    """跑一個回合；遇到「只有思考、沒有文字」的空回覆時走三層防線。

    上游 #50597 已被官方 closed as not planned，client 端兜底是唯一的路。
    """
    turn_id = f"t-{uuid.uuid4().hex[:8]}"
    res = await run_turn(prompt, state, frontend, turn_id)
    _persist_sid(res, state)

    # 兜底素材自己保管：**不可**每輪覆寫。
    # 關思考的那一輪拿不到思考文字，會把前面有內容的那份洗成空字串，
    # 第三層因此永遠讀到空值、形同死碼（cc-bot 踩過，由另一個 session 抓出來）。
    best_think = (res.all_think or "").strip()
    # 壓縮旗標同樣不可被重試輪覆寫：被切斷的是第一輪，重試輪沒被切不代表沒發生過
    saw_compact = res.compacted
    empty = 0
    try:
        while res.reply == NO_RESPONSE and not res.ask and empty < config.MAX_EMPTY_RETRY:
            empty += 1
            # 第一次仍帶思考（多半一次就過）；再失敗才動用關思考的逃生門，
            # 那是目前唯一公認有效的 workaround。
            state._no_think = empty >= 2
            await frontend.emit(make_event(
                conv, turn_id, "status",
                note=f"剛才沒收到文字，重試第 {empty}/{config.MAX_EMPTY_RETRY} 次",
            ))
            retry_id = f"t-{uuid.uuid4().hex[:8]}"
            res = await run_turn(EMPTY_RETRY_NUDGE, state, frontend, retry_id)
            _persist_sid(res, state)
            saw_compact = saw_compact or res.compacted
            if len((res.all_think or "").strip()) > len(best_think):
                best_think = (res.all_think or "").strip()
    finally:
        # 一定要還原，否則這個對話從此永久不思考。
        # 指紋隨之復原，下次對話會自動重建 client。
        state._no_think = False

    if res.reply == NO_RESPONSE and not res.ask and best_think:
        # 第三層：拿思考摘要當回覆，總比丟一句「無回應」讓整回合的推理白費好
        res.reply = f"（我想了一輪但沒能好好說出來，這是我的思路）\n\n{think_digest(best_think, 1500)}"
    if res.reply == NO_RESPONSE and res.stop_reason == "max_tokens":
        res.reply = "這次的輸出把額度用完了，話沒說完。要我換個小一點的範圍再試一次嗎？"
    res.compacted = saw_compact
    return res


async def _recheck_after_compact(
    state: ConvState, frontend: Frontend, conv: str, out: TurnOutcome,
) -> TurnResult:
    """壓縮把一則訊息的處理過程切成兩半之後，回頭核對有沒有做了卻沒說的事。

    只跑一輪，而且提示是中性的：真的什麼都沒漏時，CC 只會回一個 [[DONE]]，
    不會被逼著多講廢話（沿用 _auto_continue 那套話術的設計）。
    """
    turn_id = f"r-{uuid.uuid4().hex[:8]}"
    await frontend.emit(make_event(
        conv, turn_id, "status",
        note="剛才被自動壓縮打斷，我回頭對一下有沒有漏講",
        phase=COMPACT_PHASE,
    ))
    res = await run_turn(COMPACT_RECHECK_NUDGE, state, frontend, turn_id)
    _persist_sid(res, state)
    if res.reply and res.reply != NO_RESPONSE:
        await _say(frontend, conv, res.reply, out)
    return res


async def _auto_continue(
    said: bool, res: TurnResult, state: ConvState,
    frontend: Frontend, conv: str, out: TurnOutcome,
) -> None:
    """未完成自動續跑。

    這輪動過工具、卻沒打完成標記 [[DONE]]、也不在等使用者回答 [[WAIT]]
    → 極可能被截斷或半途停手。用中性話術補跑，最多 MAX_AUTO_CONTINUE 次。
    中性話術讓「其實已完成、只是忘了打標記」的情況無害吸收：
    CC 只會回一個 [[DONE]] 就停，不會被逼著亂做事。
    """
    cont = 0
    while (
        cont < config.MAX_AUTO_CONTINUE
        and not res.ask
        and said
        and res.used_tool
        and not res.wait
        and not res.done
    ):
        cont += 1
        turn_id = f"k-{uuid.uuid4().hex[:8]}"
        res = await run_turn(CONTINUE_NUDGE, state, frontend, turn_id)
        _persist_sid(res, state)
        out.used_tool = out.used_tool or res.used_tool
        # 續跑輪也可能問問題（補跑時才發現卡住了）。上面的 while 條件本來就會
        # 因為 res.ask 而停下，但那則回覆的選項先前**整個被丟掉**——只帶正文
        # 走 _say，使用者看到一句「要選 A 還是 B」卻沒有任何按鈕可按。
        # 順手一起帶出去，格式與第一輪完全相同。
        ask_req = parse_ask(res.ask) if res.ask else None
        out.pending_ask = out.pending_ask or ask_req is not None
        if res.reply and res.reply != NO_RESPONSE:
            await _say(frontend, conv, res.reply, out, ask=ask_req)


def _persist_sid(res: TurnResult, state: ConvState) -> None:
    if res.session_id:
        state.session_id = res.session_id
        persist(state)


async def _handle_error(
    err: CCError, state: ConvState, frontend: Frontend, conv: str,
) -> None:
    """依錯誤類型決定善後動作，再把結果告訴使用者。"""
    # 錯誤一定要進診斷檔。先前只 emit 不記，919 個回合裡查不到任何一筆錯誤紀錄，
    # 事後對「那時到底怎麼了」只能猜（見 diag.py 的說明）。
    diag.record(
        "error", conv=conv, err=err.kind, raw=diag.head(err.raw, 120),
        retryable=err.retryable, resets_at=err.resets_at,
    )
    if err.should_reset_session:
        state.session_id = None
        state.ctx_tokens = 0
        persist(state)
    if err.should_drop_client:
        # AUTH 尤其重要：帶著過期憑證出生的 client 進程不會自己撿新權杖，
        # 不丟掉就會永遠 401，連重新登入都救不回。
        await client_pool.drop(conv)
    if err.kind == "RATE_LIMIT" and err.resets_at:
        _limit_until[conv] = float(err.resets_at)
    payload: dict[str, Any] = {
        "kind": err.kind,
        "detail": err.user_msg,
        "raw": err.raw[:500],
        "retryable": err.retryable,
    }
    if err.resets_at:
        # 手機端拿它畫「15:30 回復」與倒數，不必自己解析中文
        payload["resets_at"] = float(err.resets_at)
    await frontend.emit(make_event(conv, "-", "error", **payload))
