"""方案額度：5 小時／7 天的用量百分比與重置時間。

跟 usage.py 是**兩件不同的事**，不要混在一起：
  usage.py     ── 我們自己記的帳（token 數、SDK 回報的成本），資料在本機
  plan_usage.py ── 訂閱方案還剩多少額度，資料在 Anthropic 那邊

使用者真正在意的是後者。前者的金額欄在訂閱制下多半是 0——SDK 的
total_cost_usd 是按 API 計價回報的，吃訂閱時它給不出有意義的數字。

作法沿用 cc-bot（discord_bot.py 的 fetch_usage）：拿 Claude Code 自己存在
~/.claude/.credentials.json 的 OAuth token 去打官方端點。這不是公開 API，
沒有版本保證，所以整支檔案的失敗策略一律是「回 None，畫面就不顯示這一塊」，
絕不能讓它把 /v1/usage 整條打掉。
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
ENDPOINT = "https://api.anthropic.com/api/oauth/usage"

# 額度不會秒變，而且這是每次打開設定頁都會呼叫的端點——快取一分鐘，
# 免得盯著畫面的人替他自己刷出一堆對外請求。
CACHE_SEC = 60

# 失敗也要快取。原本拿不到就直接回空陣列、不留任何紀錄，於是斷網的時候
# 每一次刷新設定頁都重新發一個 timeout=10 的對外請求：畫面要等十秒才承認
# 這塊沒東西，而那十秒佔著 to_thread 的執行緒，連按幾下就把池子塞住，
# 其他要用執行緒的工作（行事曆讀寫、用量掃描）一起卡在後面排隊。
# TTL 比成功時短很多：網路回來的時候不該讓人再等滿一分鐘才看得到額度。
FAIL_CACHE_SEC = 20

_cache: dict[str, Any] = {}

# 顯示順序與中文標題。key 是官方回應的欄位名，沒出現的就跳過
# （方案不同拿到的組合不一樣，例如沒訂 Max 就不會有分模型的那兩條）。
BUCKETS: list[tuple[str, str]] = [
    ("five_hour", "5 小時"),
    ("seven_day", "7 天"),
    ("seven_day_sonnet", "7 天 Sonnet"),
    ("seven_day_opus", "7 天 Opus"),
]


def _fetch() -> dict | None:
    """打官方端點拿原始回應。任何一步出錯都回 None。"""
    try:
        creds = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
        token = creds["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(
            ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/2.1.143",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def limits() -> list[dict]:
    """回給手機的額度清單。拿不到就回空陣列，畫面自己會少一塊。

    每筆是 {"key","label","pct","resets_at"}。pct 直接給 0-100 的數字，
    百分比的計算與夾限都留在這裡做完——手機端只負責畫，不做判斷。
    """
    now = time.time()
    if "data" in _cache and now - _cache.get("ts", 0) < CACHE_SEC:
        data = _cache["data"]
    elif now - _cache.get("fail_ts", 0) < FAIL_CACHE_SEC:
        # 剛剛才失敗過，這次直接放棄，不要再去等一輪 timeout
        return []
    else:
        data = _fetch()
        if data is None:
            _cache["fail_ts"] = now
            return []
        _cache["data"] = data
        _cache["ts"] = now
        _cache.pop("fail_ts", None)

    out: list[dict] = []
    for key, label in BUCKETS:
        obj = data.get(key)
        if not isinstance(obj, dict):
            continue
        # 欄位名兩種都出現過，utilization 優先
        pct = obj.get("utilization")
        if pct is None:
            pct = obj.get("percent_used", 0)
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        out.append({
            "key": key,
            "label": label,
            # 夾在 0-100：超用時給負數或破百會讓手機端的進度條畫爆
            "pct": max(0.0, min(100.0, pct)),
            "resets_at": str(obj.get("resets_at") or ""),
        })
    return out
