"""助理要傳給手機的檔案清單。

上傳（手機→電腦）是把 bytes 寫到 uploads 目錄；反過來（電腦→手機）**不搬檔案**，
只登記「這個路徑可以被下載」並發一個 id 出去。理由是電腦上的檔案本來就在那裡，
複製一份到 outbox 目錄只會多佔一份磁碟、還要處理原檔被改動後兩份不一致。

登記要落地（不是放記憶體）：手機可能過幾小時才點下載，服務重啟過就找不到那筆了。
"""
from __future__ import annotations

import json
import mimetypes
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from util import read_text_with_retry, replace_with_retry

OUTBOX_FILE: Path = config.DATA_DIR / "outbox.json"

# 手機一次下載的上限。超過這個數字不管網路多快都要等很久，而下載中途沒有進度可看，
# 使用者只會覺得 App 壞了——這種檔案請改用 share.ps1 給連結。
MAX_OFFER_BYTES = 256 * 1024 * 1024

# 保留幾筆登記。舊的清掉只是不能再下載，檔案本身不會動到。
KEEP = 60

_LOCK = threading.Lock()


class OutboxError(ValueError):
    """路徑有問題。訊息是寫給助理看的中文，它讀了會自己改。"""


def _load() -> dict[str, Any]:
    if not OUTBOX_FILE.exists():
        return {}
    # OSError 不接，理由同 agenda/store.py 的 _load：讀不到不等於壞掉，
    # 而回空的下一步就是把空結構存回去，登記過的檔案全部下載不到。
    try:
        data = json.loads(read_text_with_retry(OUTBOX_FILE))
    except json.JSONDecodeError:
        # 讀得到但不是合法 JSON——這才是壞檔，先隔離再回空。
        # 這裡損失的只是「可下載清單」（檔案本身沒動），不像 agenda 那麼嚴重，
        # 但沒有理由留著同一個模式等它咬人。
        _quarantine("JSON 解析失敗")
        return {}
    if not isinstance(data, dict):
        _quarantine("根結構不是物件")
        return {}
    return data


def _quarantine(why: str) -> None:
    """把讀不出來的登記檔改名保留。"""
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        OUTBOX_FILE.rename(OUTBOX_FILE.with_name(f"{OUTBOX_FILE.name}.corrupt-{stamp}"))
        print(f"[outbox] 登記檔讀取失敗（{why}），已改名保留")
    except OSError as e:
        print(f"[outbox] 壞檔隔離失敗：{e}")


def _save(data: dict[str, Any]) -> None:
    OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTBOX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    replace_with_retry(tmp, OUTBOX_FILE)


def offer(path: str, note: str = "", conv_id: str = "-") -> dict[str, Any]:
    """登記一個檔案給手機下載，回傳那筆登記（含 file_id）。

    [conv_id] 是發起這次傳檔的對話。手機端據此把卡片放進正確的聊天流，
    "-" 代表來源不明（收件匣照樣收得到，只是不會出現在任何一段對話裡）。
    """
    raw = (path or "").strip().strip('"')
    if not raw:
        raise OutboxError("要給我檔案的完整路徑")
    p = Path(raw)
    if not p.is_absolute():
        raise OutboxError(f"路徑要用絕對路徑：{raw}")
    if not p.exists():
        raise OutboxError(f"這個檔案不存在：{raw}")
    if p.is_dir():
        raise OutboxError(f"這是資料夾不是檔案，要傳整個資料夾請先壓成 zip：{raw}")
    size = p.stat().st_size
    if size > MAX_OFFER_BYTES:
        raise OutboxError(
            f"檔案太大（{size // (1024 * 1024)}MB，上限 "
            f"{MAX_OFFER_BYTES // (1024 * 1024)}MB）。這種請改用 share.ps1 給他下載連結"
        )

    item = {
        "file_id": f"f{uuid.uuid4().hex[:10]}",
        "path": str(p.resolve()),
        "name": p.name,
        "bytes": size,
        "mime": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        "note": (note or "").strip(),
        "conv_id": (conv_id or "-").strip() or "-",
        "at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
    }
    with _LOCK:
        data = _load()
        data[item["file_id"]] = item
        # 依登記時間裁切。dict 保留插入順序，但重新載入後順序來自檔案，
        # 所以照 at 排序而不是靠順序——重啟後才不會刪錯人。
        if len(data) > KEEP:
            for k in sorted(data, key=lambda k: data[k].get("at", ""))[: len(data) - KEEP]:
                data.pop(k, None)
        _save(data)
    return item


def get(file_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return _load().get(file_id)


def _at_to_ms(at: str) -> int:
    """登記時間轉 epoch 毫秒。解析不出來回 0（排到最前面，總比不見好）。

    **`at` 是本地時間而且不帶時區**（見 offer 裡的 strftime），逐字稿那邊卻是
    UTC。兩邊要排進同一條時間軸，這裡就得明確地把它當本地時間解讀——
    `astimezone()` 不帶參數正是「這個 naive 時間屬於本機時區」。漏掉這一步
    差整整八小時，而且不拋例外、不留 log，只有卡片位置會莫名其妙。

    秒數補到 59.999：`at` 只精確到分鐘，同一分鐘內要跟訊息比大小時，卡片
    應該排在後面——它的語意本來就是「這一輪講完之後才落地」。
    """
    try:
        dt = datetime.strptime(at, "%Y-%m-%dT%H:%M")
    except ValueError:
        return 0
    return int(dt.replace(second=59, microsecond=999000).astimezone().timestamp() * 1000)


def for_conv(conv_id: str) -> list[dict[str, Any]]:
    """某段對話裡傳過的檔案，舊到新。

    給 snapshot 用。App 重建畫面時逐字稿裡沒有這些卡片（那是 butler 自己造的
    東西，CC 不知道有它），只能靠時間插回原位，所以這裡一定要附上 at_ms。

    不回 path：那是這台電腦上的絕對路徑，手機端用不到，也沒有理由送出去。
    """
    cid = (conv_id or "").strip()
    if not cid or cid == "-":
        return []
    with _LOCK:
        rows = [r for r in _load().values() if (r.get("conv_id") or "-") == cid]
    rows.sort(key=lambda r: r.get("at", ""))
    return [
        {
            "file_id": r["file_id"],
            "name": r["name"],
            "bytes": r["bytes"],
            "note": r.get("note", ""),
            "mime": r.get("mime", ""),
            "at_ms": _at_to_ms(r.get("at", "")),
        }
        for r in rows
    ]


def list_recent(limit: int = 40) -> list[dict[str, Any]]:
    """由新到舊。順帶標記原檔還在不在——手機端要據此把已消失的那筆變灰，
    不然使用者點下去只會拿到一個看不懂的 404。"""
    with _LOCK:
        rows = list(_load().values())
    rows.sort(key=lambda r: r.get("at", ""), reverse=True)
    return [{**r, "gone": not Path(r["path"]).exists()} for r in rows[:limit]]
