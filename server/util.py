"""跨層共用的小工具。刻意保持極薄——放不進 engine/transport 任一層的才擺這裡。"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

# 撞上檔案鎖時重試幾次。退讓從 5ms 遞增到 50ms 封頂，30 次的總上限約 1.3 秒。
# 這個數字是量出來的不是猜的：`tests/test_p1_misc.py` 用 4 條執行緒不間斷讀同一個檔，
# 原本的「10 次 × 20ms」在那個壓力下 200 次寫入有 169 次失敗。真實負載遠低於此，
# 但既然寫入失敗等於整筆資料沒存進去，寧可多等。
_RETRIES = 30
_READ_RETRIES = 20


def _backoff(i: int) -> None:
    time.sleep(min(0.005 * (i + 1), 0.05))


def read_text_with_retry(path: Path, encoding: str = "utf-8",
                         errors: str | None = None) -> str:
    """讀檔，撞到 Windows 的短暫檔案鎖就重試。

    另一條執行緒正在 `os.replace` 換上新檔的那一瞬間，開檔會拿到
    ERROR_ACCESS_DENIED。那既不是「檔案壞了」也不是「檔案不見了」，
    是「現在剛好不行，等一下就好」。

    **這個區別很要緊。** 呼叫端若把 OSError 一律當成壞檔處理，一次毫秒級的
    檔案鎖就會讓一份好端端的 agenda.json 被改名隔離，使用者的行程整批「消失」。
    重試到底仍失敗才把例外放出去——那時候才真的是有事。
    """
    for i in range(_READ_RETRIES):
        try:
            return path.read_text(encoding=encoding, errors=errors)
        except PermissionError:
            if i == _READ_RETRIES - 1:
                raise
            _backoff(i)
    raise AssertionError("到不了這裡")   # for-else 的替代，讓型別檢查閉嘴


def replace_with_retry(src: Path, dst: Path) -> None:
    """`os.replace`，撞到 Windows 的檔案鎖就退讓重試。

    **POSIX 沒有這個問題，Windows 有。** `MoveFileEx` 對「正被別人開著」的目標檔
    會直接失敗（PermissionError / WinError 5），而這個專案所有的讀取函式都是在鎖外
    `read_text` 的——也就是說只要有人剛好在讀，這次寫入就整個拋例外。
    2026-08-17 在 devices.json 上實測到：撤銷裝置時有 4 條 verify 執行緒在讀，
    撤銷不是失效而是 500。

    重試而不是「把所有讀取都納入鎖」：讀取路徑散在五個模組（agenda/store、outbox、
    engine/usage、engine/local_usage、transport/auth），而且跨 thread 與跨進程都可能
    有讀者（restart 腳本、診斷腳本），鎖只擋得住自己進程內的那些。
    """
    for i in range(_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == _RETRIES - 1:
                raise
            _backoff(i)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """原子寫入：先寫同目錄唯一暫存檔、再 os.replace 換上。

    寫入途中崩潰／斷電不會留下半截 JSON——session、裝置清單、排程等狀態檔都吃這條。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        replace_with_retry(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
