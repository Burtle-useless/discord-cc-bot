"""使用者原話帳本：append-only，只寫人真的打過的字。

存在的理由是「查證」。對話本體會被壓縮、摘要、重寫，模型事後轉述使用者說過什麼
可能是錯的（而且聽起來很像真的）。這份檔誰都不改、只往後加，`/recall` 拿它跟
Discord 頻道歷史兩份第一手紀錄，丟回去讓模型自己對照，抓自己的記憶幻覺。

刻意不進 git（`.gitignore` 有 `user_messages.jsonl`）：內容是使用者的原話。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

FILE_NAME = "user_messages.jsonl"


def append(data_dir: Path, channel_id: int, author: str, text: str) -> None:
    """記一則。永不因寫檔失敗中斷主流程——帳本壞掉不該讓人不能講話。"""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "channel_id": int(channel_id),
            "author": author,
            "text": text,
        }
        with (data_dir / FILE_NAME).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        log.exception("寫原話帳本失敗")


def recent(data_dir: Path, channel_id: int, count: int = 30) -> list[str]:
    """這個頻道最近 count 則原話，舊到新，已格式化成一行一則。"""
    fp = data_dir / FILE_NAME
    if not fp.is_file():
        return []
    out: list[str] = []
    try:
        # errors="replace"：帳本被截在多位元組字元中間（斷電）時不能整份讀不出來
        with fp.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001 — 壞掉的那行跳過就好
                    continue
                if int(rec.get("channel_id") or 0) != int(channel_id):
                    continue
                out.append(f"[{rec.get('ts', '')}] {rec.get('author', '')}: {rec.get('text', '')}")
    except OSError:
        return []
    return out[-count:]
