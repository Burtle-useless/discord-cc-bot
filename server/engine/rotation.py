"""助理那條每天、課程對話每週換一段新的 session。

助理那條對話一路接下去不會結束。接個一個月，逐字稿可以長到幾百 MB：每句話都帶著
一大包脈絡、不時觸發壓縮，什麼指令都跑很久又燒 token；連線一被回收（閒置 15 分鐘）
或服務重啟，下一句話的 CLI 要先把整份逐字稿讀回來才接得上。官方 CLI 沒有「定期換
session」的機制——它的設計情境是一件事一個 session，換主題自己 /clear。

規則
- 換的時機只有一個：**使用者送下一則訊息、這一輪開始之前**（turn.handle_turn）。
  不在回合中途、不在 wake 回合換。
- 邊界是凌晨 4 點（每天）／週一凌晨 4 點（每週），不是午夜：半夜還在聊的那段不會被切成兩天。
- 有背景工作在跑就先不換（換＝丟掉連線＝殺掉那些工作），等下一則再看。
- 舊 session 不刪：id 收進 `past_sessions`，畫面往上捲照樣讀得到（history._session_chain），
  prompt 帶上一段逐字稿的路徑，想不起來的事自己去搜（profiles.Profile.append_text）。
- 哪種對話換、多久換一次由 profile 的 `rotate` 決定；沒設（工作對話、其他前端）就永遠不換。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from protocol import Frontend, make_event

from . import bg_notify, client_pool, diag
from .history import ROTATE_NOTES, session_started_at
from .profiles import resolve
from .state import ConvState, persist

log = logging.getLogger(__name__)

# 一天從幾點算起
DAY_START_HOUR = 4
# 留幾段舊 session 的 id。每天一段的話一年多一點；再舊的畫面上也捲不到那麼遠。
MAX_PAST = 400

def period_start(kind: str, now: datetime) -> datetime:
    """[now] 所在的這一期是從什麼時候開始的。daily＝今天 4 點；weekly＝這週一 4 點。"""
    start = now.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    if now < start:
        start -= timedelta(days=1)
    if kind == "weekly":
        start -= timedelta(days=start.weekday())
    return start


def started_at(state: ConvState) -> float | None:
    """目前這段 session 從什麼時候開始（epoch 秒）。沒記過就從逐字稿第一筆推。"""
    if state.session_started:
        return state.session_started
    if state.session_id:
        return session_started_at(state.session_id)
    return None


def due(state: ConvState, now: datetime | None = None) -> bool:
    """這條對話現在該不該換一段新的 session。"""
    kind = resolve(state.conv_id).rotate
    if not kind or not state.session_id:
        return False
    t0 = started_at(state)
    if t0 is None:
        # 逐字稿讀不出時間：當成很舊。換掉的代價只是少一段脈絡，留著的代價是永遠不換
        return True
    return datetime.fromtimestamp(t0) < period_start(kind, now or datetime.now())


async def maybe_rotate(state: ConvState, frontend: Frontend) -> bool:
    """該換就換：舊 session 收進 past_sessions、清掉目前的、丟掉連線。回傳有沒有換。

    **永不拋例外**：換不成就照舊用原本那段，不能因為這件事讓使用者的訊息失敗。
    """
    conv = state.conv_id
    try:
        if not due(state):
            return False
        if bg_notify.active(conv):
            diag.record("rotate_skip", conv=conv, why="bg_active")
            return False
    except Exception:  # noqa: BLE001
        log.exception("判斷要不要換 session 失敗（對話 %s），照舊用原本那段", conv)
        return False

    old = state.session_id
    period = resolve(conv).rotate or ""
    state.past_sessions = (list(state.past_sessions) + [old])[-MAX_PAST:]
    state.session_id = None
    state.session_started = None
    state.ctx_tokens = 0
    state.ctx_max = 0
    state.ctx_threshold = 0
    # 從這裡起就算換了：狀態已經改掉，就算後面哪一步出錯也要回 True，
    # 呼叫端才不會拿著一個空的 session 去跑壓縮
    try:
        persist(state)
        # 連線還接在舊 session 上：丟掉，這一輪 acquire 會開一條不帶 resume 的新連線
        await client_pool.drop(conv)
    except Exception:  # noqa: BLE001
        log.exception("換 session 時存檔或丟連線失敗（對話 %s）", conv)
    try:
        diag.record("rotate", conv=conv, old=old, period=period)
        log.info("對話 %s 換新 session（%s），舊的 %s 收進 past_sessions", conv, period, old)
        # 專屬事件而不是 status 的 note：note 接著就被這一輪的 turn.start 清掉，
        # 畫面上等於沒出現過（App 0.46 之前就是這樣）。分隔線要留在對話裡
        await frontend.emit(make_event(
            conv, "-", "session.rotated", note=ROTATE_NOTES.get(period, ""), period=period,
        ))
    except Exception:  # noqa: BLE001
        log.exception("換 session 的紀錄或通知失敗（對話 %s）", conv)
    return True
