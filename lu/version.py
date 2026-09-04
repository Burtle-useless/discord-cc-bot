"""版本與更新公告。

啟動時比對 `data/last_version.json`，版本變了就把 CHANGELOG 推到 UPDATE_CHANNEL，
然後把新版號寫回去。推失敗不寫回——下次啟動會再試一次。

**CHANGELOG 只寫「本版」差異，每次升版整段替換掉。** 往下追加會讓新版公告重播舊版
內容（舊 cc-bot 2026-07-21 v1.29.1 實際犯過）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

VERSION = "2.0.0"
CHANGE_TYPE = "major"   # feat / fix / major

CHANGELOG = """\
整個重寫。以前 Discord 這隻跟手機那隻各養一套引擎，同一個 bug 要修兩次、修完還會走鐘；\
現在共用同一套，那邊修好的東西這邊直接就有。

• 額度用完不再貼一句英文然後裝作沒事：畫面上一行倒數（依你的時區顯示），時間到自動把\
那則訊息重跑，訊息不會被丟掉。伺服器中途重啟也還在
• 按停止時已經打出來的字會留著，不再整段被「已停止」蓋掉
• 工具做了什麼可以點開看原文：短的直接遮罩點一下、長的按「詳細」
• 模型選單改成面板，清單直接跟官方走（Fable 之類新模型自己會出現），思考強度依模型給
• 表格不再是一堆管線符號，會轉成等寬區塊
• 背景工作跑完會自己接手講結果，不用你再問一次
• /search 語意搜尋、/recall 記憶核對、/handoff 交接稿、worktree、排程、語音全都在，\
排程到點的任務跟你打的訊息排同一條佇列（以前排程在跑時你傳的訊息會卡住）
• 語音：傳語音一直都能用，開車模式才唸回覆，閒置十分鐘自動把模型從顯卡卸下來

修掉的老問題：狀態檔讀失敗會把所有對話的 session 清空、進度訊息每 1.5 秒無條件打 API、\
按選項按鈕後回覆整段消失、限流誤判、壓縮失敗每則訊息重壓。
"""


def check_and_push(data_dir: Path):
    """回 `(要不要推, 公告內容)`。版本沒變就 (False, "")。"""
    fp = data_dir / "last_version.json"
    try:
        last = json.loads(fp.read_text(encoding="utf-8")).get("version")
    except Exception:  # noqa: BLE001 — 沒有檔＝第一次跑，當成有更新
        last = None
    if last == VERSION:
        return False, ""
    return True, CHANGELOG


def mark_pushed(data_dir: Path) -> None:
    """推成功才寫回。推失敗不寫，下次啟動再試。"""
    try:
        fp = data_dir / "last_version.json"
        tmp = fp.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": VERSION}), encoding="utf-8")
        tmp.replace(fp)
    except Exception:  # noqa: BLE001
        log.warning("寫 last_version.json 失敗")
