"""人格檔載入。

人格放在 `server/personas/*.txt`，是**純文字不是程式碼**——改語氣是個要反覆
試的事，每次都要動 Python 檔會讓人不想改，而且改壞了會連服務都起不來。

檔案裡怎麼排版都無所謂：送出前 `options.sanitize_append()` 會把整份壓成單行。
這點很要緊——含換行的 system_prompt append 會讓 SDK 的 init 握手卡死 60 秒，
而錯誤訊息完全不指向換行符。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import config

PERSONA_DIR: Path = config.SERVER_DIR / "personas"


@lru_cache(maxsize=8)
def load(name: str) -> str:
    """讀一份人格檔，回單行文字。找不到就炸——不要靜默退回預設。

    靜默 fallback 在這裡是壞主意：使用者改了 PERSONA 卻拼錯檔名時，
    症狀會是「人格沒生效」，而那跟「人格寫得不夠強」在畫面上長得一模一樣，
    他會去改人格內容而不是去看檔名。
    """
    f = PERSONA_DIR / f"{name}.txt"
    if not f.is_file():
        avail = ", ".join(sorted(p.stem for p in PERSONA_DIR.glob("*.txt"))) or "（一份都沒有）"
        raise FileNotFoundError(f"找不到人格檔 {f}。現有的：{avail}")
    lines = [
        ln for ln in f.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    ]
    return " ".join(" ".join(lines).split())
