"""coord_core 的單元測試：純函式 + 記憶體登錄表，不依賴任何測試框架。

執行：python test_coord_core.py
全程用 assert，注入固定 now 避免依賴真實時間。
"""
from __future__ import annotations

import sys

import coord_core as cc

# Windows 主控台預設 cp950，統一改 utf-8 避免印中文/符號時編碼錯誤
sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    # 1. sanitize：去反引號/錢字號/換行，壓成單一空白
    assert cc.sanitize("`rm -rf` $HOME 重構\n模組") == "rm -rf HOME 重構 模組", \
        f"清洗結果非預期：{cc.sanitize('`rm -rf` $HOME 重構\\n模組')!r}"
    # 2. sanitize：超長截斷
    assert len(cc.sanitize("a" * 300)) == 200, "應截斷到 200 字"
    # 3. sanitize：控制字元去除
    assert cc.sanitize("ab\x00cd\x07ef") == "abcdef", "控制字元應被去除"

    # 4. parse_broadcasts：抓出多筆、並把標記從文字移除
    text = "我先開工 [[COORD: 重構 auth]] 順便 [[COORD: 改 DB schema]] 完成。"
    items, clean = cc.parse_broadcasts(text)
    assert items == ["重構 auth", "改 DB schema"], f"廣播解析非預期：{items}"
    assert "[[COORD" not in clean, "clean 不應殘留標記"
    assert "我先開工" in clean and "完成。" in clean, "clean 應保留其餘文字"

    # 5. parse_broadcasts：無標記時回空清單、文字原樣（strip）
    items2, clean2 = cc.parse_broadcasts("一般回覆，沒有廣播")
    assert items2 == [] and clean2 == "一般回覆，沒有廣播", "無標記情況非預期"

    # 6. parse_broadcasts：空內容標記會被濾掉
    items3, _ = cc.parse_broadcasts("[[COORD:  `` ]]")
    assert items3 == [], f"空內容應被濾除：{items3}"

    # 7. Registry：update + recent（排除自己）
    reg = cc.Registry(ttl_sec=1800, max_items=8)
    reg.update(1, "alpha", "重構 auth", now=1000.0)
    reg.update(2, "beta", "改 DB schema", now=1000.0)
    got = [a.channel_id for a in reg.recent(exclude=1, now=1000.0)]
    assert got == [2], f"recent(exclude=1) 非預期：{got}"

    # 8. Registry：超過 TTL 的活動不出現
    assert reg.recent(now=1000.0 + 2000) == [], "超過 TTL 應視為過期"

    # 9. Registry：新到舊排序、每頻道只留最新
    reg.update(3, "g", "old", now=1000.0)
    reg.update(3, "g", "new", now=1010.0)   # 覆蓋同頻道舊紀錄
    reg.update(4, "h", "later", now=1005.0)
    rec = reg.recent(now=1010.0)
    assert rec[0].channel_id == 3 and rec[0].task == "new", "應留最新且新者在前"
    assert all(a.channel_id != 3 or a.task == "new" for a in rec), "同頻道不應有兩筆"

    # 10. format_for_prompt：含內容、排除指定頻道
    s = reg.format_for_prompt(exclude=3, now=1010.0)
    assert "#h" in s and "later" in s, f"摘要應含 beta/h 的活動：{s}"
    assert "#g" not in s, "被排除的頻道不應出現"
    # 過期後摘要為空字串
    assert reg.format_for_prompt(now=1010.0 + 99999) == "", "全過期應回空字串"

    print("✅ 全部通過（10 項）")


if __name__ == "__main__":
    main()
