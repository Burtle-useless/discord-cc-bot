"""拿 Claude Code 自己的 OAuth 登入去打官方端點（方案額度、模型清單）。

token 在 `~/.claude/.credentials.json`，由 CLI 自己維護與續期；這裡只讀、不寫、不印。
這不是公開 API 的正式用法，沒有版本保證，所以唯一的失敗策略是回 None——
呼叫端一律把它當「這塊資料暫時拿不到」，絕不能讓它拖垮別的東西。
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"


def get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 10) -> Any | None:
    """GET 一個官方端點，回解析後的 JSON。任何一步出錯（沒登入、斷網、非 2xx）都回 None。"""
    try:
        creds = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
        token = creds["claudeAiOauth"]["accessToken"]
        h = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/2.1.143",
            "Content-Type": "application/json",
        }
        h.update(headers or {})
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:  # noqa: BLE001 — 見模組說明：失敗一律當沒有
        return None
