"""狀態遷移：舊 cc-bot 的檔 → 陸（engine）的格式。全部在暫存目錄裡跑。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lu import migrate_state  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


def test_convert() -> None:
    print("\n[格式轉換]")
    old = {
        # 早期只存字串
        "111": "sid-a",
        # 後期是 dict，且帶 wt（worktree，engine 沒這個概念）
        "222": {"session_id": "sid-b", "model": "claude-opus-5", "effort": "high",
                "cwd": "C:\\code\\demo", "wt": "feature-x"},
        # 沒有 session 的空殼頻道
        "333": {"session_id": None, "model": None, "effort": None, "cwd": ""},
        # 不是頻道 id 的鍵要跳過
        "note": "垃圾",
    }
    sessions, by_sid = migrate_state.convert_sessions(old, "C:\\default")
    check("三個頻道都轉過來", set(sessions) == {"dc:111", "dc:222", "dc:333"}, str(list(sessions)))
    check("純字串的也吃", sessions["dc:111"]["session_id"] == "sid-a")
    check("dict 的保留模型與思考", sessions["dc:222"]["model"] == "claude-opus-5"
          and sessions["dc:222"]["effort"] == "high")
    check("wt 欄位丟掉", "wt" not in sessions["dc:222"], str(sessions["dc:222"]))
    check("forked_from 補 None", all(r["forked_from"] is None for r in sessions.values()))
    check("沒填 cwd 的用預設", sessions["dc:333"]["cwd"] == "C:\\default")
    check("反查表只收有 session 的", by_sid == {"sid-a": "dc:111", "sid-b": "dc:222"}, str(by_sid))

    titles = migrate_state.convert_titles(
        {"sid-a": "設定檔的事", "sid-b": "  改槍工具  ", "sid-gone": "沒有頻道了"}, by_sid)
    check("標題換成 conv_id 當鍵", titles == {"dc:111": "設定檔的事", "dc:222": "改槍工具"},
          str(titles))

    check("帳號預設對齊 engine",
          migrate_state.convert_defaults({"model": "sonnet", "effort": "high"})
          == {"model": "sonnet", "effort": "high"})
    check("空的帳號預設回 None", migrate_state.convert_defaults({}) is None)


def test_run() -> None:
    print("\n[整份跑一次]")
    with tempfile.TemporaryDirectory() as d:
        old = Path(d) / "old"
        new = Path(d) / "data"
        old.mkdir()
        (old / "discord_session.json").write_text(
            json.dumps({"111": "sid-a"}), encoding="utf-8")
        (old / "session_titles.json").write_text(
            json.dumps({"sid-a": "第一條"}), encoding="utf-8")
        (old / "allowed_users.json").write_text("[123]", encoding="utf-8")
        (old / "schedules.json").write_text("[]", encoding="utf-8")
        # 壞掉的檔不能讓整個遷移停下來
        (old / "account_defaults.json").write_text("{壞掉", encoding="utf-8")

        st = migrate_state.run(old, new, "C:\\code\\demo", verbose=False)
        check("session 有寫出來", (new / "session.json").is_file() and st["sessions"] == 1)
        check("標題有寫出來", json.loads((new / "titles.json").read_text(encoding="utf-8"))
              == {"dc:111": "第一條"})
        check("壞掉的帳號預設跳過", st["defaults"] == 0 and not (new / "account_defaults.json").exists())
        check("其餘原樣複製", (new / "allowed_users.json").is_file()
              and (new / "schedules.json").is_file() and st["copied"] == 2)
        check("舊檔一個都沒動", (old / "discord_session.json").is_file()
              and json.loads((old / "discord_session.json").read_text(encoding="utf-8"))
              == {"111": "sid-a"})

        # 重跑一次：結果一樣，而且不覆蓋新目錄裡已經跑動過的檔
        (new / "allowed_users.json").write_text("[123,456]", encoding="utf-8")
        st2 = migrate_state.run(old, new, "C:\\code\\demo", verbose=False)
        check("重跑安全", st2["sessions"] == 1)
        check("不覆蓋跑動過的檔",
              (new / "allowed_users.json").read_text(encoding="utf-8") == "[123,456]")


def main() -> int:
    test_convert()
    test_run()
    print(f"\n{'全部通過' if not FAILED else '失敗：' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
