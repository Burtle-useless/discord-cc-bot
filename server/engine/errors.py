"""CC 錯誤分類與善後策略。

分類的目的不是為了漂亮的錯誤訊息，是為了決定**善後動作**：
哪些該重試、哪些該丟掉 client 重生、哪些該清掉 session 重來。
把這三件事搞錯，症狀會是「重新登入也救不回的 401」或「越重試越糟」。

**額度用盡是錯誤，不是回覆。** 2026-09-02 之前 CLI 回「You've hit your session
limit · resets 3pm」這段文字時，runner 把它當一般回覆定稿、`_auto_continue` 再補跑
一輪、手機推播「做完了」——使用者看到的是助理頂著自己的臉講一句英文。SDK 其實有
結構化訊號：`RateLimitEvent(status="rejected", resets_at=…)` 與 `ResultMessage.is_error`，
runner 現在認這兩個，這裡只負責把它們變成 `CCError`（帶 `resets_at`）。
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta

ErrKind = str


def classify(err: str) -> ErrKind:
    """把例外訊息歸類。比對字串是刻意的——SDK 不提供結構化錯誤碼。"""
    s = (err or "").lower()
    if "exceeded maximum buffer size" in s or "failed to decode json" in s:
        return "INPUT_TOO_LARGE"
    if "prompt is too long" in s or ("400" in s and "too long" in s):
        return "CONTEXT_FULL"
    if "529" in s or "overloaded" in s:
        return "OVERLOADED"
    # 「hit your … limit」是 CLI 額度用盡時放進回覆文字的句型（session limit／
    # usage limit／weekly limit 都是這個形狀），跟 API 的 429 是同一件事。
    if ("429" in s or "rate_limit" in s or "rate limit" in s
            or "hit your" in s and "limit" in s or "usage limit" in s):
        return "RATE_LIMIT"
    if "failed to start claude" in s or "winerror 267" in s or "目錄名稱無效" in s:
        return "STARTUP"
    if "401" in s or "credential" in s or "authentication" in s or "unauthorized" in s:
        return "AUTH"
    if "control request timeout" in s or "initialize" in s:
        return "INIT_TIMEOUT"
    return "UNKNOWN"


# 值得自動重試的（暫時性問題）
RETRYABLE: frozenset[str] = frozenset({"OVERLOADED", "RATE_LIMIT", "INIT_TIMEOUT"})

# 必須清掉 session 重來的——context 撐爆時 resume 回去只會再爆一次
RESET_SESSION: frozenset[str] = frozenset({"CONTEXT_FULL"})

# 必須丟棄長駐 client 的。
# AUTH 尤其重要：401 多半是「client 進程帶著過期憑證出生」，
# 那個進程不會自己去撿新權杖，不丟掉就會永遠 401——重新登入也沒用。
DROP_CLIENT: frozenset[str] = frozenset({"AUTH", "CONTEXT_FULL", "STARTUP", "INIT_TIMEOUT"})

USER_FACING: dict[str, str] = {
    "INPUT_TOO_LARGE": "這次的輸入太大，我吃不下。分批給我或先讓我讀檔案。",
    "CONTEXT_FULL": "這個對話太長了，我先把它壓縮過再繼續。",
    "OVERLOADED": "Anthropic 那邊現在滿載，我等一下再試。",
    "RATE_LIMIT": "用量到上限了，要等額度回復。",
    "AUTH": "登入憑證失效了，要在電腦上重新 claude /login。",
    "STARTUP": "Claude Code 起不來，多半是工作目錄不存在。",
    "TIMEOUT": "太久沒有任何回應，我把這回合中止了。",
    "INIT_TIMEOUT": "Claude Code 初始化卡住了，我重開一個試試。",
    "UNKNOWN": "出了點狀況。",
}


# 額度用盡時，回復時刻在多久以內就把訊息留在佇列裡等它自動重跑。官方終端機只叫你等，
# 但助理是在手機上用的：人送一句話出門，回來看到「額度用完」比看到答案糟得多。
# 超過這個時間（例如週額度要等三天）就不等了，人自己決定。
AUTO_RESUME_MAX_SEC = 6 * 3600


_RESETS_RE = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def parse_resets_at(text: str, now: float | None = None) -> float | None:
    """從 CLI 的限流文字（「You've hit your session limit · resets 3:30pm (Asia/Taipei)」）
    解析回復時刻。CLI 通常會另外送 RateLimitEvent 帶 resets_at，這是它沒送時的後備：
    沒有時刻就沒辦法自動續跑，只能叫人自己回來。

    時間當本機時區（那串文字本來就是照本機時區印的）。今天的那個時刻已經過了
    就算明天。
    """
    m = _RESETS_RE.search(text or "")
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    base = datetime.fromtimestamp(now if now is not None else time.time())
    target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target.timestamp() <= base.timestamp():
        target = target + timedelta(days=1)
    return target.timestamp()


def reset_label(resets_at: float | None) -> str:
    """把 epoch 秒變成「15:30」或「明天 09:00」這種人看的字。"""
    if not resets_at:
        return ""
    dt = datetime.fromtimestamp(resets_at)
    today = datetime.now().date()
    if dt.date() == today:
        return f"{dt:%H:%M}"
    if (dt.date() - today).days == 1:
        return f"明天 {dt:%H:%M}"
    return f"{dt:%m/%d %H:%M}"


class CCError(Exception):
    """已分類的 CC 錯誤。

    刻意用普通 class 而不是 dataclass：`@dataclass(slots=True)` 會重建類別物件，
    使 `__post_init__` 裡 zero-arg 的 `super()` 指向**舊**的類別，
    實例化時直接拋 "obj is not an instance or subtype of type"。
    Exception 子類就照傳統寫法寫，別為了風格改。

    `resets_at`：額度何時回復（epoch 秒），只有 RATE_LIMIT 且 CLI 有給時才有。
    transport 靠它決定要不要把訊息留在佇列裡等到那時候自動重跑。
    """

    def __init__(self, kind: ErrKind, raw: str, resets_at: float | None = None) -> None:
        self.kind = kind
        self.raw = raw
        self.resets_at = resets_at
        super().__init__(f"{kind}: {raw[:200]}")

    @property
    def user_msg(self) -> str:
        if self.kind == "RATE_LIMIT" and self.resets_at:
            when = reset_label(self.resets_at)
            wait = self.resets_at - time.time()
            if 0 < wait <= AUTO_RESUME_MAX_SEC:
                return f"用量到上限了，{when} 回復後我會自動接著做。"
            if wait > 0:
                return f"用量到上限了，{when} 才會回復。"
        return USER_FACING.get(self.kind, USER_FACING["UNKNOWN"])

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    @property
    def should_drop_client(self) -> bool:
        return self.kind in DROP_CLIENT

    @property
    def should_reset_session(self) -> bool:
        return self.kind in RESET_SESSION


def wrap(exc: BaseException) -> CCError:
    """把任意例外轉成已分類的 CCError。"""
    if isinstance(exc, CCError):
        return exc
    # TimeoutError 的 str() 是空字串，交給 classify 只會得到 UNKNOWN，
    # 而它其實是最常見的一種失敗（CC 連續無輸出達 INACTIVITY_TIMEOUT）。
    if isinstance(exc, asyncio.TimeoutError):
        return CCError(kind="TIMEOUT", raw="連續無輸出超過逾時上限")
    return CCError(kind=classify(str(exc)), raw=f"{type(exc).__name__}: {exc}")
