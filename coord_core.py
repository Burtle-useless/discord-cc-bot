"""跨頻道協作（AI Lounge）核心邏輯。

與 Discord 無關的純邏輯，方便獨立測試。概念：每個頻道是一個獨立的 Claude
session，彼此看不到對方在做什麼；本模組負責
1) 解析 Claude 回覆裡的 [[COORD: ...]] 廣播，
2) 維護「哪個頻道正在做什麼」的記憶體登錄表，
3) 產生要注入其他頻道 prompt 的「近期活動摘要」，
讓多個 session 在同一台機器上協作時能彼此知會、避免互相踩到。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

# 解析 Claude 輸出裡的協作廣播，例如：[[COORD: 我要開始重構 auth 模組]]
COORD_MARKER = re.compile(r"\[\[COORD:\s*(.+?)\s*\]\]")

# 注入 prompt / 對外廣播前要清掉的危險字元：反引號與錢字號（CC 管線與 Discord
# 格式都敏感），連同控制字元一起去除，避免干擾 init 控制訊息或洗版。
_DANGEROUS = re.compile(r"[`$\x00-\x08\x0b\x0c\x0e-\x1f]")


def sanitize(text: str, limit: int = 200) -> str:
    """清洗自由文字：去危險字元、把連續空白（含換行）壓成單一空白、截斷長度。"""
    s = _DANGEROUS.sub("", text)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_broadcasts(text: str) -> tuple[list[str], str]:
    """從 Claude 回覆抓出所有 [[COORD:]] 內容。

    回傳 (廣播清單, 去除標記後的文字)。廣播內容會先經 sanitize，空字串會被濾掉。
    """
    items = [s for s in (sanitize(m) for m in COORD_MARKER.findall(text)) if s]
    clean = COORD_MARKER.sub("", text).strip()
    return items, clean


@dataclass
class Activity:
    """某頻道最近一次廣播的活動。"""
    channel_id: int
    name: str
    task: str
    ts: float


class Registry:
    """記憶體版「頻道 → 最近活動」登錄表（每頻道只保留最新一筆）。

    單一 event loop 內存取，不需鎖。過期（超過 ttl）的活動在讀取時自動略過。
    """

    def __init__(self, ttl_sec: float = 1800.0, max_items: int = 8) -> None:
        self._items: dict[int, Activity] = {}
        self._ttl = ttl_sec      # 活動視為「近期」的有效秒數，預設 30 分鐘
        self._max = max_items    # 摘要最多列出幾筆，避免 prompt 過長

    def update(self, channel_id: int, name: str, task: str,
               now: float | None = None) -> None:
        """記錄某頻道的最新任務（覆蓋舊的）。now 可注入以利測試。"""
        ts = now if now is not None else time.time()
        self._items[channel_id] = Activity(channel_id, name, task, ts)

    def recent(self, exclude: int | None = None,
               now: float | None = None) -> list[Activity]:
        """回傳未過期的活動（可排除指定頻道），新到舊排序、最多 max_items 筆。"""
        t = now if now is not None else time.time()
        items = [a for a in self._items.values()
                 if a.channel_id != exclude and t - a.ts <= self._ttl]
        items.sort(key=lambda a: a.ts, reverse=True)
        return items[:self._max]

    def format_for_prompt(self, exclude: int | None = None,
                          now: float | None = None) -> str:
        """產生要注入 user prompt 的近期活動摘要（bullet 清單）；無活動回空字串。"""
        t = now if now is not None else time.time()
        lines: list[str] = []
        for a in self.recent(exclude=exclude, now=t):
            mins = int((t - a.ts) // 60)
            when = "just now" if mins <= 0 else f"{mins}m ago"
            lines.append(f"- #{a.name}: {a.task} ({when})")
        return "\n".join(lines)
