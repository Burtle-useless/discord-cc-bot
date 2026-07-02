"""i18n 字串表的一致性測試。

執行：python test_i18n.py
不依賴任何測試框架，全程用 assert，最後印出結果。
防的是最常見的 i18n 腐化：一邊加了 key 另一邊忘了、或兩邊 format 佔位符不一致
（後者會在執行期炸 KeyError，而且只在切到另一個語言時才炸）。
"""
from __future__ import annotations

import re
import sys

import i18n

# Windows 主控台預設 cp950，統一改 utf-8 避免印中文/符號時編碼錯誤
sys.stdout.reconfigure(encoding="utf-8")

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

passed = 0


def ok(name: str) -> None:
    global passed
    passed += 1
    print(f"  ✓ {name}")


def main() -> None:
    en = i18n._STRINGS["en"]
    zh = i18n._STRINGS["zh-TW"]

    # 1. 兩邊 key 集合必須完全一致（缺 key 會靜默退英文，使用者看到中英混雜才發現）
    only_en = set(en) - set(zh)
    only_zh = set(zh) - set(en)
    assert not only_en, f"zh-TW 缺少 key：{sorted(only_en)}"
    assert not only_zh, f"en 缺少 key：{sorted(only_zh)}"
    ok(f"en / zh-TW key 完全對齊（{len(en)} 個）")

    # 2. 同一個 key 的 format 佔位符必須一致（不一致＝切語言時 KeyError）
    for key in en:
        ph_en = set(_PLACEHOLDER.findall(en[key]))
        ph_zh = set(_PLACEHOLDER.findall(zh[key]))
        assert ph_en == ph_zh, f"key '{key}' 佔位符不一致：en={ph_en} zh={ph_zh}"
    ok("所有 key 的 format 佔位符一致")

    # 3. t() 的行為：存在的 key 回字串、不存在的 key 回 key 本身（不炸）
    assert i18n.t("no_response")
    assert i18n.t("this_key_does_not_exist") == "this_key_does_not_exist"
    ok("t() 缺鍵時回退 key 本身、不拋例外")

    # 4. 帶參數的 key 實際 format 一次，確保佔位符名稱沒打錯
    assert "3" in i18n.t("retry_notice", kind="X", delay="3", n=1, max=4)
    assert "abc" in i18n.t("renamed", title="abc")
    ok("帶參數字串可正常 format")

    print(f"✅ 全部通過（{passed} 項）")


if __name__ == "__main__":
    main()
