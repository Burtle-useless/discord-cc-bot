"""陸：cc-bot 的骨架——Discord transport，引擎是 repo 自帶的 `server/engine`／`server/protocol`。

**「陸」（lu）只是內部代號**，用來在原始碼與 log 裡指稱這一版。
使用者看得到的字串一律不出現它：bot 對外就是 Claude Code／CC，也不自我介紹。
`lu/i18n.py` 裡沒有任何一處提到它，改文案時請維持這條。

套件裡沒有任何回合邏輯：排隊、插話、限流等待、續跑、壓縮、錯誤善後全在
`server/engine`——**那份引擎跟這個 repo 一起 clone 下來，不必另外裝**。
這裡只做三件事：把 Discord 訊息交給 `engine.worker.Worker`、
把引擎發出的語意事件畫成 Discord 訊息（`frontend`／`render`／`canvas`）、
管側欄頻道與 slash 指令。

啟動順序寫在 `__main__.py`；**先 `bootstrap.prepare()` 再 import 任何 engine 模組**，
否則引擎的 config 會讀到錯的資料目錄。
"""
from __future__ import annotations

__version__ = "2.0.0-a1"
