"""用量記帳：每個回合花了多少 token、多少錢。

按「日」彙總而不是逐筆流水帳。理由是這份資料唯一的用途是回答
「今天用了多少、這個月用了多少、錢花在哪個模型」——逐筆存要多寫一套
輪替與裁切邏輯，而且手機上也不會有人捲一整年的明細。

成本直接取 SDK 給的 `total_cost_usd`，不自己按價目表算：
價目表會變，而算錯的數字比沒有數字更糟。
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

import config
from util import read_text_with_retry, replace_with_retry

USAGE_FILE: Path = config.DATA_DIR / "usage.json"

# 留多久。三個月足夠看出趨勢，再久的資料沒人會回頭查，
# 而檔案每回合都要整份重寫，讓它無限長大只會愈寫愈慢。
KEEP_DAYS = 92

_LOCK = threading.Lock()

# 一天的空白帳。欄位名刻意短——這份檔案每回合重寫一次。
_ZERO: dict[str, Any] = {
    "turns": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0,
}


def _empty() -> dict[str, Any]:
    return {"days": {}}


def _load() -> dict[str, Any]:
    if not USAGE_FILE.exists():
        return _empty()
    try:
        # 加重試是為了不要把「有人正在換檔」誤判成壞檔——那會讓下一次寫入
        # 用空結構蓋掉 92 天的用量歷史。真的壞掉才回空，那條取捨維持原樣。
        data = json.loads(read_text_with_retry(USAGE_FILE))
    except (OSError, json.JSONDecodeError):
        return _empty()          # 壞檔就當沒有，用量表不值得讓服務起不來
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        return _empty()
    return data


def _save(data: dict[str, Any]) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USAGE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    replace_with_retry(tmp, USAGE_FILE)


def _prune(days: dict[str, Any]) -> None:
    if len(days) <= KEEP_DAYS:
        return
    for k in sorted(days)[: len(days) - KEEP_DAYS]:
        days.pop(k, None)


def _add(bucket: dict[str, Any], src: dict[str, Any]) -> None:
    """把一筆用量加進某個彙總桶（就地修改）。"""
    for k in ("turns", "in", "out", "cache_read", "cache_write"):
        bucket[k] = int(bucket.get(k, 0)) + int(src.get(k, 0))
    bucket["cost"] = round(float(bucket.get("cost", 0.0)) + float(src.get("cost", 0.0)), 6)


def record(model: str, usage: dict[str, Any] | None, cost: float | None) -> None:
    """記一個回合。任何一步出錯都吞掉——記帳失敗不該把使用者的回合拖垮。"""
    try:
        u = usage or {}
        one = {
            "turns": 1,
            "in": int(u.get("input_tokens", 0) or 0),
            "out": int(u.get("output_tokens", 0) or 0),
            "cache_read": int(u.get("cache_read_input_tokens", 0) or 0),
            "cache_write": int(u.get("cache_creation_input_tokens", 0) or 0),
            "cost": float(cost or 0.0),
        }
        today = date.today().isoformat()
        name = model or "unknown"
        with _LOCK:
            data = _load()
            days = data["days"]
            day = days.setdefault(today, {**_ZERO, "models": {}})
            day.setdefault("models", {})
            _add(day, one)
            _add(day["models"].setdefault(name, dict(_ZERO)), one)
            _prune(days)
            _save(data)
    except Exception:       # noqa: BLE001 — 記帳是附帶功能，絕不影響主流程
        pass


def _blank() -> dict[str, Any]:
    return dict(_ZERO)


def report(span: int = 14) -> dict[str, Any]:
    """今日／本月總計、最近 span 天的逐日、以及本月各模型分佈。

    逐日一定補滿到 span 天（沒用的日子給零）：手機端要畫長條圖，
    缺天數的話橫軸會被壓縮，「昨天沒用」看起來會變成「昨天沒發生過」。
    """
    with _LOCK:
        days: dict[str, Any] = _load()["days"]

    today = date.today()
    today_key = today.isoformat()
    month_key = today.strftime("%Y-%m")

    month = _blank()
    models: dict[str, dict[str, Any]] = {}
    for key, row in days.items():
        if not key.startswith(month_key):
            continue
        _add(month, row)
        for name, m in (row.get("models") or {}).items():
            _add(models.setdefault(name, _blank()), m)

    ordinal = today.toordinal()
    series = []
    for i in range(span - 1, -1, -1):
        key = date.fromordinal(ordinal - i).isoformat()
        row = days.get(key) or _blank()
        series.append({
            "date": key,
            "in": int(row.get("in", 0)),
            "out": int(row.get("out", 0)),
            "cost": round(float(row.get("cost", 0.0)), 4),
            "turns": int(row.get("turns", 0)),
        })

    return {
        "today": {**_blank(), **{k: v for k, v in (days.get(today_key) or {}).items()
                                 if k != "models"}},
        "month": month,
        "month_key": month_key,
        "days": series,
        "models": sorted(
            ({"model": n, **v} for n, v in models.items()),
            key=lambda r: r["cost"], reverse=True,
        ),
        "since": min(days) if days else today_key,
        "now": datetime.now().strftime("%Y-%m-%dT%H:%M"),
    }
