"""排程：讀寫 schedules.json、算下一次觸發、到點把任務丟進 Worker。

**到點是 `worker.submit`，不是直接跑回合。** 舊 cc-bot 的排程迴圈自己呼叫
`run_claude`，等於跟使用者訊息走兩條不同的路：頻道正在處理時整輪只好跳過，
執行期間進來的訊息也沒人消化（舊版註解自承「排程執行期間不會回頭消化佇列」）。
改走 worker 之後排程只是佇列裡的一則訊息，插話、排隊、順序全部由 Worker 統一管，
那個問題自動消失，這裡不必再有任何「忙碌就跳過」的判斷。

時區：**全程用本機時區的 naive datetime**。舊版 prompt 寫死「台灣時間 UTC+8」、
迴圈卻拿 naive 的 `datetime.now()` 去比，機器不在 UTC+8 時兩邊會差好幾個小時。
現在 prompt 只說「這是本機時間」，模型給回來的字串若帶時區就先 `astimezone()`
轉本地再去掉 tzinfo（見 `parse_local`）。

檔案格式沿用舊的（遷移腳本原樣複製過去），一筆長這樣：
    {"id": "8碼", "user_id": 123, "channel_id": 456, "task": "看信",
     "cron": "0 8 * * *", "next_run": "2026-09-05T08:00:00", "created_at": "..."}
`cron` 空字串＝一次性任務，跑完就刪。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .i18n import t
from .profile import conv_id as conv_of

if TYPE_CHECKING:
    from .bootstrap import BotContext

log = logging.getLogger(__name__)

# 迴圈檢查間隔（秒）。跟舊版一樣 30 秒：cron 最細只到分鐘，30 秒綽綽有餘。
TICK = 30.0

# 餵給解析器的「現在」用英文星期縮寫，避免主控台編碼與語系兩個變數攪在一起
_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_JSON_BLOB = re.compile(r"\{.*\}", re.DOTALL)

try:
    from croniter import croniter as _croniter
    HAS_CRONITER = True
except Exception:  # noqa: BLE001 — 沒裝就只支援一次性排程，不讓整包 import 失敗
    _croniter = None
    HAS_CRONITER = False


# ── 純函式（測試打的就是這一段）──────────────────────────────────────────
def load(path: Path) -> list[dict[str, Any]]:
    """讀排程檔。檔案不在、壞掉、或根本不是 list 一律回空清單——排程壞了不該讓 bot 起不來。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def save(path: Path, items: list[dict[str, Any]]) -> None:
    """原子換檔寫回。寫一半崩潰只會留下 .tmp，正本永遠是完整的 JSON。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def parse_local(s: str) -> datetime | None:
    """ISO 字串 → 本機時區的 naive datetime。認不得回 None。

    模型偶爾會給帶時區的字串（`2026-09-05T08:00:00+08:00`）。直接拿去跟 naive 的
    `datetime.now()` 比會 TypeError，舊版因此整輪排程停擺，所以一律先轉本地再脫掉 tzinfo。
    """
    try:
        dt = datetime.fromisoformat(str(s).strip())
    except (TypeError, ValueError):
        return None
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo is not None else dt


def next_after(cron: str, after: datetime) -> datetime | None:
    """cron 表達式在 `after` 之後的下一次觸發時刻。空字串／無效／沒裝 croniter 都回 None。

    回 None 對呼叫端的意思統一是「這筆沒有下一次了」——一次性任務跑完即刪，
    cron 壞掉的也刪（留著只會每 30 秒重試一次同一個錯）。
    """
    expr = (cron or "").strip()
    if not expr or not HAS_CRONITER:
        return None
    try:
        return _croniter(expr, after).get_next(datetime)
    except Exception:  # noqa: BLE001 — croniter 對壞表達式丟的例外型別不只一種
        return None


def valid_cron(cron: str) -> bool:
    """這串 cron 排得出下一次嗎。空字串（一次性）不算有效 cron。"""
    return next_after(cron, datetime(2000, 1, 1)) is not None


def is_due(item: dict[str, Any], now: datetime) -> bool:
    """這筆該執行了嗎。`next_run` 認不得就當作沒到期（保留該筆，不默默丟掉使用者的排程）。"""
    nxt = parse_local(str(item.get("next_run") or ""))
    return nxt is not None and now >= nxt


def extract_json(text: str) -> dict[str, Any] | None:
    """從模型回覆裡挖出那包 JSON。挖不到或不是物件回 None。

    刻意用貪婪的 `{.*}`：模型常把 JSON 包在 ```json 圍籬或一段說明裡。
    """
    m = _JSON_BLOB.search(text or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group())
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def now_hint(now: datetime) -> str:
    """餵給解析器的「現在幾點幾分週幾」。少了它，「今晚八點」會被算到昨天或明天。"""
    return now.strftime("%Y-%m-%d %H:%M ") + _WD[now.weekday()]


def make_entry(*, user_id: int, channel_id: int, task: str, cron: str,
               next_run: datetime, now: datetime | None = None) -> dict[str, Any]:
    """組一筆排程。id 取 uuid4 前 8 碼，跟舊版一樣（使用者要拿它來刪）。"""
    return {
        "id": uuid.uuid4().hex[:8],
        "user_id": int(user_id),
        "channel_id": int(channel_id),
        "task": task,
        "cron": (cron or "").strip(),
        "next_run": next_run.isoformat(),
        "created_at": (now or datetime.now()).isoformat(),
    }


# ── 背景迴圈 ────────────────────────────────────────────────────────────
class Scheduler:
    """排程檔的持有者兼背景迴圈。一隻 bot 一個。"""

    def __init__(self, ctx: "BotContext") -> None:
        self.ctx = ctx
        self.path: Path = ctx.settings.data_dir / "schedules.json"
        self._task: asyncio.Task | None = None

    # ── 生命週期 ──
    def start(self) -> None:
        """開背景迴圈。重複呼叫無害（已經在跑就什麼都不做）。"""
        if self._task is not None and not self._task.done():
            return
        self._task = self.ctx.keep_task(asyncio.create_task(self._loop()))

    def stop(self) -> None:
        """停背景迴圈。`ctx.close()` 也會連帶取消它，這裡是給單獨關掉用的。"""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ── 資料 ──
    def list(self) -> list[dict[str, Any]]:
        return load(self.path)

    def add(self, *, user_id: int, channel_id: int, task: str, cron: str,
            next_run: datetime) -> dict[str, Any]:
        entry = make_entry(user_id=user_id, channel_id=channel_id, task=task,
                           cron=cron, next_run=next_run)
        items = load(self.path)
        items.append(entry)
        save(self.path, items)
        return entry

    def remove(self, sched_id: str) -> bool:
        """刪一筆，回傳有沒有刪到。"""
        items = load(self.path)
        keep = [x for x in items if str(x.get("id")) != str(sched_id)]
        if len(keep) == len(items):
            return False
        save(self.path, keep)
        return True

    # ── 迴圈本體 ──
    async def _loop(self) -> None:
        client = self.ctx.client
        if client is not None:
            await client.wait_until_ready()
        while True:
            await asyncio.sleep(TICK)
            try:
                await self.tick(datetime.now())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 單輪出事不能讓整條排程死掉
                log.exception("排程迴圈這一輪出錯")

    async def tick(self, now: datetime) -> None:
        """檢查一輪：到期的丟給 Worker，然後重算 next_run（沒有下一次就刪掉）。"""
        items = load(self.path)
        keep: list[dict[str, Any]] = []
        changed = False
        for item in items:
            if not is_due(item, now):
                keep.append(item)
                continue
            await self._fire(item)
            nxt = next_after(str(item.get("cron") or ""), now)
            changed = True
            if nxt is None:            # 一次性任務，或 cron 壞掉 → 這筆結束
                continue
            item["next_run"] = nxt.isoformat()
            keep.append(item)
        if changed:
            save(self.path, keep)

    async def _fire(self, item: dict[str, Any]) -> None:
        """把任務交給 Worker。頻道已經被刪掉就跳過（送出去也沒人看得到，白燒 token）。"""
        cid = int(item.get("channel_id") or 0)
        text = str(item.get("task") or "").strip()
        if not text:
            return
        ch = self.ctx.client.get_channel(cid) if self.ctx.client else None
        if self.ctx.client is not None and ch is None:
            log.warning("排程 %s 的頻道 %s 已不存在，這次跳過", item.get("id"), cid)
            return
        if ch is not None:
            try:
                await ch.send(t("run_schedule", task=text))
            except Exception:  # noqa: BLE001 — 只是提示，送不出去不影響任務本身
                log.warning("排程提示訊息送不出去（頻道 %s）", cid)
        # 走 worker：跟使用者訊息同一條佇列，插話與排隊由它統一決定
        await self.ctx.worker.submit(conv_of(cid), text, t("schedule_src"))
