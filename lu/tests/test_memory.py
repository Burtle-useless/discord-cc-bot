"""原話帳本與搜尋的純函式測試。不連 Discord、不載 embedding 模型。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env  # noqa: E402

_env.setup("lu-memory-")

from lu import ledger, semantic  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


def test_ledger() -> None:
    print("\n[原話帳本]")
    with tempfile.TemporaryDirectory() as d:
        data = Path(d)
        check("沒有檔就回空", ledger.recent(data, 1) == [])
        ledger.append(data, 111, "小明", "第一句")
        ledger.append(data, 222, "別人", "別的頻道")
        ledger.append(data, 111, "小明", "第二句")
        rows = ledger.recent(data, 111)
        check("只回這個頻道的", len(rows) == 2 and all("別的頻道" not in r for r in rows),
              str(rows))
        check("順序是舊到新", "第一句" in rows[0] and "第二句" in rows[1], str(rows))
        check("有時間與作者", "小明" in rows[0] and rows[0].startswith("["), rows[0])
        check("count 取最後幾則", ledger.recent(data, 111, 1)[0].endswith("第二句"),
              str(ledger.recent(data, 111, 1)))

        # 壞掉的行要跳過，不能讓整份讀不出來
        with (data / ledger.FILE_NAME).open("a", encoding="utf-8") as f:
            f.write("這行不是 json\n")
        ledger.append(data, 111, "小明", "第三句")
        check("壞行跳過、其餘照讀", len(ledger.recent(data, 111)) == 3,
              str(ledger.recent(data, 111)))

    # 目錄不存在時 append 不能炸（主流程不該因為帳本壞掉而中斷）
    ledger.append(Path("Z:/沒有這個磁碟"), 1, "x", "y")
    check("寫不進去也不拋例外", True)


def test_chunk() -> None:
    print("\n[切塊]")
    check("空的回空清單", semantic.chunk("") == [] and semantic.chunk("   ") == [])
    check("短的一塊", semantic.chunk("短短一句") == ["短短一句"])
    text = "x" * 1000
    ch = semantic.chunk(text)
    check("長的切成多塊", len(ch) > 1, str(len(ch)))
    check("每塊不超過上限", all(len(c) <= semantic.CHUNK_SIZE for c in ch),
          str([len(c) for c in ch]))
    check("塊之間有重疊", ch[0][-semantic.CHUNK_OVERLAP:] == ch[1][:semantic.CHUNK_OVERLAP],
          f"{ch[0][-5:]} vs {ch[1][:5]}")
    joined = "".join(ch)
    check("切完不掉字", len(joined) >= len(text), f"{len(joined)} vs {len(text)}")


def test_literal() -> None:
    print("\n[字面搜尋]")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        jf = root / "abc123.jsonl"
        rec = {"type": "user", "timestamp": "2026-09-01T00:00:00Z",
               "message": {"content": [{"type": "text", "text": "關於防跌預警系統的感測器"}]}}
        jf.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        files = {"abc123": jf}
        hits = semantic.literal("防跌預警", files, 10)
        check("命中一筆", len(hits) == 1, str(hits))
        check("摘要含關鍵字", "防跌預警" in hits[0]["snippet"], hits[0]["snippet"])
        check("帶得出 session_id", hits[0]["session_id"] == "abc123")
        check("沒命中就空", semantic.literal("完全不相關的字", files, 10) == [])
        check("空查詢回空", semantic.literal("  ", files, 10) == [])
        # 檔案被刪掉不能炸
        jf.unlink()
        check("檔沒了不炸", semantic.literal("防跌預警", files, 10) == [])


def test_fallback() -> None:
    print("\n[沒有 fastembed 就退回字面]")
    saved = semantic._unavailable
    semantic._unavailable = True     # 假裝載不到模型
    try:
        check("_get_model 回 None", semantic._get_model() is None)
        check("_semantic 回 None（呼叫端據此退回）",
              semantic._semantic("x", [], {}, 5) is None)
    finally:
        semantic._unavailable = saved


def main() -> int:
    test_ledger()
    test_chunk()
    test_literal()
    test_fallback()
    print(f"\n{'全部通過' if not FAILED else '失敗：' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
