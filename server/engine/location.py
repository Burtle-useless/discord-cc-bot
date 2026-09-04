"""使用者現在在哪：最後已知位置的儲存，以及給助理用的查詢工具。

位置從兩條路徑進來，都寫進同一筆快取：

1. **手機定期回報**——服務跑著的時候每 15 分鐘送一次（見 App 的 Locator）。
2. **助理主動問**——快取太舊時 `where_am_i` 發一次請求下去，手機抓完就回。

有了第一條，第二條多半不會觸發：預設接受五分鐘內的位置，而定期回報一直在餵。
這正是要的效果——**問位置不該讓使用者的手機當場開一次 GPS**，那既慢又耗電。

**只存最新一筆，不存軌跡。** 每次回報直接覆寫整個檔案。要做「他今天去過哪」
就得在這裡累積一份位置歷史，那是完全不同性質的東西：一份長期的行蹤紀錄躺在
這台電腦上，而助理回答問題根本用不到它。

檔案格式跟 agenda 一致——本機時間的裸字串，不帶時區。
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

import config
from util import atomic_write_text, read_text_with_retry

LOCATION_FILE = config.DATA_DIR / "location.json"

# 快取幾分鐘內算「還能用」。設 5 分鐘是因為人在這個尺度上通常還在同一個地方，
# 而助理問位置的典型情境（附近有什麼、通勤要多久）也不需要更精細。
DEFAULT_MAX_AGE_MIN = 5

# 跟手機要位置最多等幾秒。GPS 冷啟動可能要十幾秒，但助理等太久整個回合就卡著；
# 等不到會退回快取，不是完全失敗。
ASK_TIMEOUT_SEC = 20.0

# 由 transport 層注入：真正去跟手機要位置的那個動作。
# engine 不認識 HTTP 也不認識 SSE，跟 agenda_tools.set_change_hook 同一套做法。
_ask_device: Callable[[], Awaitable[dict[str, Any] | None]] | None = None


def set_locate_hook(fn: Callable[[], Awaitable[dict[str, Any] | None]]) -> None:
    global _ask_device
    _ask_device = fn


def _num(v: Any, lo: float, hi: float) -> float:
    """轉成範圍內的浮點數，否則拋 ValueError（呼叫端轉成 400）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"不是數字：{v!r}") from None
    if not lo <= f <= hi:
        raise ValueError(f"超出範圍：{f}")
    return f


def save(payload: dict[str, Any]) -> dict[str, Any]:
    """存下一筆位置回報，回存進去的那筆。座標不合法就拋 ValueError。"""
    rec = {
        "lat": _num(payload.get("lat"), -90, 90),
        "lon": _num(payload.get("lon"), -180, 180),
        # 地址是手機用 Geocoder 轉的，轉不出來就空字串——沒有網路時會發生，
        # 這時仍然要把座標存下來，總比什麼都沒有好。
        "address": str(payload.get("address") or "").strip(),
        # 精度（公尺）決定這筆位置能拿來回答什麼：50 公尺可以說在哪條路上，
        # 2000 公尺（基地台定位）只能說在哪個區。助理要看得到才不會亂講。
        "accuracy_m": round(_num(payload.get("accuracy_m", 0), 0, 10 ** 6), 1),
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    atomic_write_text(LOCATION_FILE, json.dumps(rec, ensure_ascii=False, indent=2))
    return rec


def last_known() -> dict[str, Any] | None:
    """讀出最後已知位置。沒有、或檔案壞了都回 None。"""
    try:
        rec = json.loads(read_text_with_retry(LOCATION_FILE))
    except (OSError, json.JSONDecodeError):
        return None
    return rec if isinstance(rec, dict) and "lat" in rec else None


def age_min(rec: dict[str, Any]) -> float:
    """這筆位置是幾分鐘前的。時間戳壞掉就回一個大到不會被當成新鮮的值。"""
    try:
        then = datetime.fromisoformat(str(rec.get("ts")))
    except (TypeError, ValueError):
        return float("inf")
    return max(0.0, (datetime.now() - then).total_seconds() / 60)


def _ok(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False)}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _describe(rec: dict[str, Any], source: str) -> dict[str, Any]:
    out = dict(rec)
    out["age_min"] = round(age_min(rec), 1)
    out["source"] = source
    return out


@tool(
    "where_am_i",
    "查使用者現在人在哪裡，回座標與地址。"
    "問「我在哪」「附近有什麼」「回家要多久」這類需要知道所在地的問題時用它。"
    "他的手機每 15 分鐘會自己回報一次，所以這通常是直接讀已經存好的位置，很快；"
    "只有存的那筆太舊時才會真的去問手機一次。",
    {
        "type": "object",
        "properties": {
            "max_age_min": {
                "type": "number",
                "description":
                    f"幾分鐘內的舊位置可以直接拿來用，預設 {DEFAULT_MAX_AGE_MIN}。"
                    "他在移動中、或要用位置做導航之類的判斷時填 0 強制重抓。",
            },
        },
        "required": [],
    },
)
async def where_am_i(args: dict[str, Any]) -> dict[str, Any]:
    max_age = args.get("max_age_min")
    max_age = DEFAULT_MAX_AGE_MIN if max_age is None else max(0.0, float(max_age))

    cached = last_known()
    if cached is not None and age_min(cached) <= max_age:
        return _ok(_describe(cached, "快取"))

    fresh = None if _ask_device is None else await _ask_device()
    if fresh is not None and "lat" in fresh:
        return _ok(_describe(fresh, "剛抓的"))

    # 手機沒回應（沒開、沒網路、權限關著）。有舊的就給舊的，但**一定要講清楚
    # 這是多久以前的**——助理把三小時前的位置當成現在，會講出「你現在在學校」
    # 這種很有自信的錯話。
    if cached is not None:
        out = _describe(cached, "手機沒回應，這是最後一次知道的位置")
        why = (fresh or {}).get("error") if isinstance(fresh, dict) else None
        if why:
            out["device_error"] = why
        return _ok(out)
    return _err("拿不到位置：手機沒有回應，本機也沒有存過任何位置。"
                "可能是 App 沒開、沒連上，或定位權限沒給。")


SERVER_NAME = "location"
SERVER = create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=[where_am_i])
