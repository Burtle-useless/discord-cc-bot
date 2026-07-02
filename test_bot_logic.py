"""discord_bot 核心純邏輯的單元測試（錯誤分類、context 規則、切塊、危險偵測、命名）。

執行：python test_bot_logic.py
不依賴任何測試框架，全程用 assert。用假環境變數 import 主模組——
import 只會建構物件、不會連 Discord，因此可以直接測模組裡的純函式。
"""
from __future__ import annotations

import os
import sys

# 主模組 import 時檢查必填環境變數；測試用假值即可（不會真的連線）
os.environ.setdefault("DISCORD_TOKEN", "test-dummy-token")
os.environ.setdefault("ALLOWED_USER", "1")
os.environ.setdefault("BOT_LANG", "en")   # 固定語言，讓字串相關斷言穩定

import discord_bot as d  # noqa: E402（環境變數要先設好才能 import）

# Windows 主控台預設 cp950，統一改 utf-8 避免印中文/符號時編碼錯誤
sys.stdout.reconfigure(encoding="utf-8")

passed = 0


def ok(name: str) -> None:
    global passed
    passed += 1
    print(f"  ✓ {name}")


def test_classify_cc_error() -> None:
    """錯誤分類直接決定「給使用者看什麼」與「要不要重試」，分錯＝行為錯。"""
    cases = [
        ("exceeded maximum buffer size", "INPUT_TOO_LARGE"),
        ("Failed to decode JSON", "INPUT_TOO_LARGE"),
        ("400 prompt is too long", "CONTEXT_FULL"),
        ("Error 529 overloaded_error", "OVERLOADED"),
        ("HTTP 429 too many requests", "RATE_LIMIT"),
        ("rate limit exceeded", "RATE_LIMIT"),
        ("Failed to start Claude Code", "STARTUP"),
        ("WinError 267 目錄名稱無效", "STARTUP"),
        ("401 unauthorized", "AUTH"),
        ("invalid credential provided", "AUTH"),
        ("Control request timeout", "INIT_TIMEOUT"),
        ("initialize timed out", "INIT_TIMEOUT"),
        ("something completely different", "UNKNOWN"),
    ]
    for raw, want in cases:
        got = d.classify_cc_error(raw)
        assert got == want, f"classify({raw!r}) = {got}，預期 {want}"
    # 可重試集合的成員資格（改動這裡會直接改變重試行為，值得釘住）
    assert d._RETRYABLE == {"OVERLOADED", "RATE_LIMIT", "INIT_TIMEOUT"}
    assert d._RESET_SESSION == {"CONTEXT_FULL"}
    ok("classify_cc_error 全部案例 + 重試集合")


def test_context_limit_for() -> None:
    """官方 1M context 三條規則：[1m] 別名強制 1M；Opus 在 max/team/enterprise 自動 1M；其餘 200K。"""
    f = d.context_limit_for
    assert f("claude-sonnet-4-6", "pro") == 200_000
    assert f("claude-sonnet-4-6[1m]", "pro") == 1_000_000       # 規則 1：別名強制
    assert f("claude-opus-4-8", "max") == 1_000_000             # 規則 2：Opus 自動
    assert f("claude-opus-4-8", "enterprise") == 1_000_000
    assert f("claude-opus-4-8", "pro") == 200_000               # Opus 在 Pro 不自動
    assert f("claude-opus-4-8", "unknown") == 200_000
    assert f("claude-haiku-4-5-20251001", "max") == 200_000     # 非 Opus 不自動
    assert f("", "unknown") == 200_000                          # 空模型退 DEFAULT_MODEL
    ok("context_limit_for 官方三規則")


def test_chunk_text() -> None:
    """/search 索引切塊：空字串、單塊、多塊重疊。"""
    assert d._chunk_text("") == []
    assert d._chunk_text("   ") == []
    assert d._chunk_text("short text") == ["short text"]
    long = "x" * 1000
    chunks = d._chunk_text(long)
    assert all(len(c) <= d._CHUNK_SIZE for c in chunks)
    assert chunks[0] == long[:d._CHUNK_SIZE]
    step = d._CHUNK_SIZE - d._CHUNK_OVERLAP
    assert chunks[1] == long[step:step + d._CHUNK_SIZE]          # 相鄰塊帶重疊
    joined_coverage = step * (len(chunks) - 1) + len(chunks[-1])
    assert joined_coverage >= len(long)                          # 尾端沒有漏字
    ok("_chunk_text 空/單塊/重疊/覆蓋")


def test_needs_confirm() -> None:
    """危險指令偵測：開關行為 + 常見高危樣式命中 + 詞界不誤殺。"""
    prev = d._CONFIRM_ENABLED
    try:
        d._CONFIRM_ENABLED = True
        hits = [
            "rm -rf /tmp/x", "rm -f file", "git push origin main",
            "git push --force", "git reset --hard HEAD~1", "git clean -fd",
            "git branch -D feature", "shutdown /s /t 0", "taskkill /f /im x.exe",
            "Remove-Item -Recurse -Force C:/x", "Stop-Process -Name x",
            "reg delete HKCU/x", "format d:",
        ]
        for cmd in hits:
            assert d._needs_confirm("Bash", {"command": cmd}), f"應攔未攔：{cmd}"
            assert d._needs_confirm("PowerShell", {"command": cmd})
        safe = [
            "echo hello", "git status", "git commit -m x", "git pull",
            "please confirm the file",      # confirm 內含 rm，詞界必須擋住誤判
            "ls -la", "python -m pytest",
        ]
        for cmd in safe:
            assert not d._needs_confirm("Bash", {"command": cmd}), f"誤攔：{cmd}"
        # 只看 Bash/PowerShell；其他工具（如 Write）不在此機制管轄
        assert not d._needs_confirm("Write", {"file_path": "x", "content": "rm -rf /"})
        # 總開關關閉時一律放行
        d._CONFIRM_ENABLED = False
        assert not d._needs_confirm("Bash", {"command": "rm -rf /"})
    finally:
        d._CONFIRM_ENABLED = prev
    ok("_needs_confirm 開關/命中/詞界")


def test_channel_naming() -> None:
    """側欄頻道命名：清洗、worktree 前綴加/去。"""
    assert d._safe_channel_name("  hello\nworld  ") == "hello world"
    assert d._safe_channel_name("") == d.t("untitled_chat")      # 空標題退預設名
    assert d._strip_wt_prefix(f"{d.WT_PREFIX} foo") == "foo"
    assert d._strip_wt_prefix("foo") == "foo"
    assert d._channel_display_name("foo", True) == f"{d.WT_PREFIX} foo"
    assert d._channel_display_name(f"{d.WT_PREFIX} foo", False) == "foo"   # 關 worktree 時去前綴
    ok("側欄命名清洗與 🌿 前綴")


def test_reply_cleanup() -> None:
    """回覆清理：[[MILESTONE:]] 標記移除、[[FILE:]] 路徑抽取。"""
    assert d._MILESTONE_RE.sub("", "a [[MILESTONE: done]] b") == "a  b"
    paths = d._FILE_MARKER.findall("see [[FILE: C:/tmp/a.pdf]] and [[FILE:D:/b.md]]")
    assert paths == ["C:/tmp/a.pdf", "D:/b.md"]
    ok("MILESTONE 移除與 FILE 標記抽取")


def main() -> None:
    test_classify_cc_error()
    test_context_limit_for()
    test_chunk_text()
    test_needs_confirm()
    test_channel_naming()
    test_reply_cleanup()
    print(f"✅ 全部通過（{passed} 項）")


if __name__ == "__main__":
    main()
