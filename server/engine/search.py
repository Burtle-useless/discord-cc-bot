"""對話搜尋：關鍵字掃 butler 自己的 session 逐字稿。

對照 cc-bot 的 /search，但先做關鍵字版。語意搜尋（e5 向量）要多載一個
1024 維模型＋建索引，等關鍵字版證明不夠用再說。
"""
from __future__ import annotations

import json
from pathlib import Path

import config

from .state import _load_map, get_title


def _iter_texts(jf: Path):
    """逐行抽出 session jsonl 裡的 user/assistant 純文字。"""
    # errors="replace" 不能省：逐字稿被截在多位元組字元中間（斷電、CC 被 kill）
    # 時解碼會拋 UnicodeDecodeError，而呼叫端只接 OSError——那條路是 500，
    # 而且只要壞檔還躺在那裡就**每次搜尋都** 500。
    with jf.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") not in ("user", "assistant"):
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                yield rec["type"], content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        yield rec["type"], block.get("text", "")


# 同一個對話最多回幾筆。先前沒有這個上限，一條長對話裡出現 20 次關鍵字
# 就把名額整個吃光，其他對話明明也命中卻永遠排不進來——而「這個詞我在哪條
# 對話講過」正是搜尋最主要的用途，只回一條對話等於沒回答那個問題。
_PER_CONV = 3


def search(query: str, limit: int = 20) -> list[dict]:
    """關鍵字搜尋所有 butler 對話，回 [{conv_id, title, role, snippet}]。

    每條對話最多貢獻 `_PER_CONV` 筆，讓結果橫跨多條對話而不是被一條佔滿。
    """
    q = query.strip().lower()
    if not q:
        return []
    claude_home = config.claude_projects_dir()
    out: list[dict] = []
    for conv_id, rec in _load_map().items():
        sid = rec.get("session_id")
        if not sid:
            continue
        for jf in claude_home.glob(f"*/{sid}.jsonl"):
            # 標題一條對話只查一次。get_title 每次呼叫都重讀狀態檔
            # （state.py 沒有快取），擺在命中迴圈裡就是命中幾筆讀幾次同一個檔。
            title = get_title(conv_id) or conv_id
            hits = 0
            try:
                for role, text in _iter_texts(jf):
                    low = text.lower()
                    pos = low.find(q)
                    if pos < 0:
                        continue
                    # 命中點前後各取一段當摘要，避免回整篇
                    start = max(0, pos - 30)
                    snippet = text[start:pos + len(q) + 50].replace("\n", " ")
                    out.append({
                        "conv_id": conv_id,
                        "title": title,
                        "role": role,
                        "snippet": ("…" if start > 0 else "") + snippet + "…",
                    })
                    if len(out) >= limit:
                        return out
                    hits += 1
                    if hits >= _PER_CONV:
                        break       # 這條夠了，把名額讓給其他對話
            except OSError:
                pass
            break
    return out
