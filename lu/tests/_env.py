"""測試共用的路徑與環境設定。**每支測試都要在 import lu 之前先 import 這個模組。**

做三件事，順序不能換：
  1. repo 根目錄放上 sys.path（才 import 得到 `lu`）
  2. `server/` 放上 sys.path（`engine`／`protocol`／`config` 在那裡）
  3. `BUTLER_DATA_DIR` 指到一個暫存目錄

第 3 步慢一步就會寫進 `server/data/`：引擎的 `config` 在 import 當下就把
資料目錄凍成常數，而 `lu.scheduler`／`lu.frontend` 都會一路把它帶進來。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# repo 根目錄（lu/tests/ 的上兩層）
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

def butler_server() -> Path:
    """引擎的 server 目錄。預設是 repo 自帶的那份，`BUTLER_SERVER_DIR` 可以覆寫。"""
    raw = (os.environ.get("BUTLER_SERVER_DIR") or "").strip()
    path = Path(raw).expanduser().resolve() if raw else REPO_ROOT / "server"
    if not (path / "engine").is_dir():
        raise RuntimeError(
            f"{path} 底下沒有 engine 目錄。引擎正常會跟這個 repo 一起 clone 下來；\n"
            "如果是自己刪掉或只抓了部分檔案，重新 clone 一次最快。",
        )
    return path


def setup(prefix: str = "lu-test-") -> Path:
    """排好 sys.path 與暫存資料目錄，回傳那個暫存目錄。可重複呼叫。"""
    for p in (str(butler_server()), str(REPO_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)
    tmp = os.environ.get("BUTLER_DATA_DIR")
    if not tmp:
        tmp = tempfile.mkdtemp(prefix=prefix)
        os.environ["BUTLER_DATA_DIR"] = tmp
    os.environ.setdefault("BUTLER_CWD", tmp)
    return Path(tmp)
