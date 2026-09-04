"""回合診斷紀錄。

存在的理由：2026-08-17 使用者回報「助理的回覆整段看不到」，而查證時發現
**引擎層一行日誌都沒有**——CC 的逐字稿證明模型確實吐了 770 字，但那些字
究竟是沒被 runner 讀到、沒被折進 reply、還是送出去了而 App 沒畫，
三種可能在事後完全無法分辨。只能靠讀程式碼推測，推了一輪還是不確定。

所以這裡記的不是「發生了什麼事」的流水帳，而是**專門用來切開那三種可能**的
幾個數字：runner 收到哪些訊息、折出多長的回覆、有沒有真的 emit 出去。
一個回合一行，欄位固定，出事時 grep 一次就有答案。

刻意不用 logging：那套要設定 handler、格式、輪替，而這裡要的是機器可讀的
定長紀錄，直接寫 jsonl 更省事也更好分析。
"""
from __future__ import annotations

import json
import time
from typing import Any

import config

DIAG_FILE = config.DATA_DIR / "turn_diag.jsonl"

# 超過就從頭來過。診斷資料沒有長期保存價值，出事時看的一定是最近幾回合，
# 而放著不管會在幾個月後長成幾百 MB 沒人發現。
_MAX_BYTES = 8 * 1024 * 1024


def record(kind: str, **fields: Any) -> None:
    """寫一行診斷。**任何失敗都吞掉**——診斷不可以反過來弄壞正在跑的回合。"""
    try:
        DIAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if DIAG_FILE.exists() and DIAG_FILE.stat().st_size > _MAX_BYTES:
            DIAG_FILE.unlink(missing_ok=True)
        row = {"t": time.strftime("%m-%d %H:%M:%S"), "kind": kind, **fields}
        with DIAG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def head(s: str | None, n: int = 60) -> str:
    """取開頭幾個字並壓成單行，用來認出「是哪一則」而不是保存內容。"""
    return " ".join((s or "").split())[:n]
