"""排程純函式測試：cron 推算、到期判斷、排程檔讀寫、壞檔不炸。

不連 Discord、不呼叫任何模型——這裡驗的全是 `lu.scheduler` 裡不需要外部世界的那一半。
import 前先把 BUTLER_DATA_DIR 指到暫存目錄：`lu.scheduler` 會 import `lu.profile`，
而它一路帶進引擎的 `config`，那個模組在 import 當下就把資料目錄凍成常數了。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env  # noqa: E402

_TMP = _env.setup("lu-sched-")

from lu import scheduler as sc  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


def test_parse_local() -> None:
    print("\n[ISO 時間解析]")
    check("naive 原樣", sc.parse_local("2026-09-05T08:00:00") == datetime(2026, 9, 5, 8, 0))
    # 帶時區的字串舊版會 TypeError 炸掉整輪排程；現在一律轉本地再脫時區
    aware = sc.parse_local("2026-09-05T08:00:00+08:00")
    check("帶時區的轉成 naive", aware is not None and aware.tzinfo is None, repr(aware))
    check("空字串回 None", sc.parse_local("") is None)
    check("亂寫回 None", sc.parse_local("明天早上八點") is None)
    check("None 也不炸", sc.parse_local(None) is None)  # type: ignore[arg-type]


def test_next_after() -> None:
    print("\n[cron 推下一次]")
    now = datetime(2026, 9, 4, 9, 30)
    nxt = sc.next_after("0 8 * * *", now)
    check("每天八點 → 隔天八點", nxt == datetime(2026, 9, 5, 8, 0), repr(nxt))
    check("下一次一定晚於現在", nxt is not None and nxt > now)
    check("每小時整點", sc.next_after("0 * * * *", now) == datetime(2026, 9, 4, 10, 0))
    # 週一 = weekday 1；2026-09-04 是週五，下一個週一是 09-07
    check("每週一九點", sc.next_after("0 9 * * 1", now) == datetime(2026, 9, 7, 9, 0),
          repr(sc.next_after("0 9 * * 1", now)))
    check("空字串沒有下一次", sc.next_after("", now) is None)
    check("壞表達式沒有下一次", sc.next_after("每天八點", now) is None)
    check("欄位數不對也不炸", sc.next_after("0 8", now) is None)
    check("valid_cron 認得好的", sc.valid_cron("*/5 * * * *"))
    check("valid_cron 擋掉壞的", not sc.valid_cron("nope"))
    check("valid_cron 不把空字串當有效", not sc.valid_cron(""))


def test_is_due() -> None:
    print("\n[到期判斷]")
    now = datetime(2026, 9, 4, 10, 0)
    check("過去的到期", sc.is_due({"next_run": "2026-09-04T09:59:00"}, now))
    check("剛好到點算到期", sc.is_due({"next_run": "2026-09-04T10:00:00"}, now))
    check("未來的不到期", not sc.is_due({"next_run": "2026-09-04T10:01:00"}, now))
    check("沒有 next_run 不到期", not sc.is_due({}, now))
    check("壞掉的 next_run 不到期", not sc.is_due({"next_run": "???"}, now))
    # 帶時區的到期判斷不能炸（舊版就是死在這裡）
    aware = {"next_run": datetime(2026, 9, 4, 9, 0).astimezone().isoformat()}
    check("帶時區的照樣比得出來", sc.is_due(aware, now), aware["next_run"])


def test_extract_json() -> None:
    print("\n[挖模型回覆裡的 JSON]")
    good = sc.extract_json('{"task": "看信", "cron": "0 8 * * *", "next_run": "2026-09-05T08:00:00"}')
    check("純 JSON", good is not None and good["cron"] == "0 8 * * *", repr(good))
    fenced = sc.extract_json('好的：\n```json\n{"task": "看信", "cron": ""}\n```\n以上')
    check("包在圍籬裡也挖得到", fenced is not None and fenced["task"] == "看信", repr(fenced))
    check("沒有 JSON 回 None", sc.extract_json("我不知道") is None)
    check("壞 JSON 回 None", sc.extract_json('{"task": ') is None)
    check("不是物件回 None", sc.extract_json("[1, 2, 3]") is None)
    check("空字串回 None", sc.extract_json("") is None)


def test_file_io() -> None:
    print("\n[排程檔讀寫]")
    p = _TMP / "schedules.json"
    check("檔案不存在回空清單", sc.load(p) == [])

    items = [sc.make_entry(user_id=1, channel_id=999, task="看信", cron="0 8 * * *",
                           next_run=datetime(2026, 9, 5, 8, 0))]
    sc.save(p, items)
    back = sc.load(p)
    check("存了讀得回來", len(back) == 1 and back[0]["task"] == "看信", repr(back))
    check("id 是 8 碼", len(back[0]["id"]) == 8, back[0]["id"])
    check("channel_id 存成整數", isinstance(back[0]["channel_id"], int))
    check("中文沒有被轉成 \\u", "看信" in p.read_text(encoding="utf-8"))
    check("沒有留下 .tmp", not p.with_name(p.name + ".tmp").exists())

    # 壞檔一律當作沒有排程，不能讓 bot 起不來
    p.write_text("{ 這不是 JSON", encoding="utf-8")
    check("壞檔回空清單", sc.load(p) == [])
    p.write_text('{"a": 1}', encoding="utf-8")
    check("不是 list 回空清單", sc.load(p) == [])
    p.write_text('[1, "x", {"id": "ok"}]', encoding="utf-8")
    check("清單裡的雜物被濾掉", sc.load(p) == [{"id": "ok"}], repr(sc.load(p)))
    p.write_text("", encoding="utf-8")
    check("空檔回空清單", sc.load(p) == [])

    # 存回去要能被 json 直接讀（格式沿用舊 cc-bot 的 schedules.json）
    sc.save(p, items)
    check("仍是標準 JSON 陣列", isinstance(json.loads(p.read_text(encoding="utf-8")), list))


def test_now_hint() -> None:
    print("\n[餵給解析器的現在時刻]")
    hint = sc.now_hint(datetime(2026, 9, 4, 9, 5))
    check("有日期時間", hint.startswith("2026-09-04 09:05"), hint)
    check("有星期（2026-09-04 是週五）", hint.endswith("Fri"), hint)


def main() -> int:
    if not sc.HAS_CRONITER:
        print("!! croniter 沒裝，cron 相關測試會全滅")
    test_parse_local()
    test_next_after()
    test_is_due()
    test_extract_json()
    test_file_io()
    test_now_hint()
    print(f"\n{'全部通過' if not FAILED else '失敗：' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
