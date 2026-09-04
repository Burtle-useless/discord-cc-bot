"""看板資料管理。

結構：**階段分欄、卡片在欄底下**（手機版把傳統左右三欄改成上下堆疊）。

  待辦（5）
    ├ 助理－看板拖放   急
    └ 論文－補圖表
  進行中（2）
    └ ...
  完成（3・另有 12 張收起來）

一張卡片是一件能被推進、能被標成完成的工作。屬於哪個企劃直接寫在標題裡
（「助理－看板拖放」這樣），不另外分組——同時在跑的線超過七條時，泳道分組
反而比寫在標題裡更難掃。

使用者要的三個維度各自的位置：
  計畫 → 卡片標題自己帶
  進度 → 卡片在哪一欄
  緊急 → urgent 旗標（會把卡片頂到該欄最上面）

「完成」欄只顯示最近幾張，其餘算進 hidden 數字。不這樣做的話幾週後整頁
都是已完成的東西，看板就廢了。
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

import config

_LOCK = threading.Lock()
_DATA = config.DATA_DIR / "kanban.json"

# 看板由上到下的欄位順序。
STATUSES = ["todo", "doing", "done"]
STATUS_LABELS: dict[str, str] = {
    "todo": "待辦",
    "doing": "進行中",
    "done": "完成",
}

# 「完成」欄最多顯示幾張，其餘收起來只報數量。
DONE_VISIBLE = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, dict]:
    if not _DATA.exists():
        return {}
    try:
        data = json.loads(_DATA.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(cards: dict[str, dict]) -> None:
    _DATA.parent.mkdir(parents=True, exist_ok=True)
    _DATA.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")


def _live(cards: dict[str, dict]) -> list[dict]:
    return [c for c in cards.values() if not c.get("archived")]


def _norm_status(s: str | None) -> str:
    return s if s in STATUSES else "todo"


def get_board() -> dict:
    """整張板子，已經照顯示順序排好。

    回傳 {"columns": [{status, label, total, hidden, cards: [...]}, ...]}
    ——直接就是畫面由上到下的順序，App 不必再排一次（排序規則只寫一份，
    兩邊各排一次遲早會走鐘）。
    """
    with _LOCK:
        cards = _live(_load())

    groups: dict[str, list[dict]] = {s: [] for s in STATUSES}
    for c in cards:
        groups[_norm_status(c.get("status"))].append(c)

    columns: list[dict] = []
    for s in STATUSES:
        items = groups[s]
        total = len(items)
        if s == "done":
            # 完成的照時間新的在前，只留最近幾張。
            items.sort(key=lambda c: c.get("last_touch", ""), reverse=True)
            shown = items[:DONE_VISIBLE]
            hidden = total - len(shown)
        else:
            # 待辦與進行中：純照手動拖出來的順序。
            # 刻意**不**讓急件自動置頂——那會讓「拖到的位置」跟「看到的位置」對不上，
            # 拖完卡片自己跳走。急件靠紅標呈現就夠，排哪裡由使用者自己決定。
            items.sort(key=lambda c: c.get("order", 0))
            shown = items
            hidden = 0
        columns.append({
            "status": s,
            "label": STATUS_LABELS[s],
            "total": total,
            "hidden": hidden,
            "cards": shown,
        })
    return {"columns": columns}


def add_card(title: str, status: str = "todo",
             urgent: bool = False, note: str = "") -> dict:
    """新增一件工作，排在該欄最後面。"""
    status = _norm_status(status)
    with _LOCK:
        cards = _load()
        same = [c for c in cards.values()
                if not c.get("archived") and _norm_status(c.get("status")) == status]
        nxt = max((c.get("order", 0) for c in same), default=-1) + 1
        card: dict = {
            "id": uuid.uuid4().hex[:12],
            "title": title,
            "status": status,
            "urgent": bool(urgent),
            "order": nxt,
            "note": note,
            "last_touch": _now_iso(),
            "archived": False,
        }
        cards[card["id"]] = card
        _save(cards)
    return card


def update_card(card_id: str, title: str | None = None, status: str | None = None,
                urgent: bool | None = None, note: str | None = None) -> dict | None:
    """改一張卡片。只傳想改的欄位；找不到回 None。

    換欄（改 status）時把 order 排到新欄最後面——沿用舊欄的 order 會跟
    新欄既有的卡片撞號，順序就亂了。要指定落點請用 reorder。
    """
    with _LOCK:
        cards = _load()
        card = cards.get(card_id)
        if card is None:
            return None
        if title is not None:
            card["title"] = title
        if status is not None and status in STATUSES and status != card.get("status"):
            same = [c for c in cards.values()
                    if not c.get("archived") and c["id"] != card_id
                    and _norm_status(c.get("status")) == status]
            card["status"] = status
            card["order"] = max((c.get("order", 0) for c in same), default=-1) + 1
        if urgent is not None:
            card["urgent"] = bool(urgent)
        if note is not None:
            card["note"] = note
        card["last_touch"] = _now_iso()
        _save(cards)
    return card


def reorder(card_id: str, status: str, order: int) -> dict | None:
    """拖放落點：把卡片插進 status 欄的第 order 個位置。

    做法是「抽出來、插回去、整欄重新編號 0..n-1」，不是只改被拖的那張。
    只改一張的話會撞號（兩張同 order，排序退化成看字典順序，拖完位置亂跳），
    而且 App 送來的 order 是「畫面上的第幾個」，跟資料裡的 order 值本來就
    不保證對得上，重新編號才能讓兩邊永遠一致。
    """
    status = _norm_status(status)
    with _LOCK:
        cards = _load()
        card = cards.get(card_id)
        if card is None:
            return None
        rest = [c for c in cards.values()
                if not c.get("archived") and c["id"] != card_id
                and _norm_status(c.get("status")) == status]
        rest.sort(key=lambda c: c.get("order", 0))
        card["status"] = status
        card["last_touch"] = _now_iso()
        rest.insert(max(0, min(order, len(rest))), card)
        for i, c in enumerate(rest):
            c["order"] = i
        _save(cards)
    return card


def archive_card(card_id: str) -> bool:
    with _LOCK:
        cards = _load()
        if card_id not in cards:
            return False
        cards[card_id]["archived"] = True
        cards[card_id]["last_touch"] = _now_iso()
        _save(cards)
    return True
