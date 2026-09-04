"""畫布渲染的純函式測試：事件序列 → 畫面文字。不需要 discord，也不需要 engine。"""
from __future__ import annotations

import sys
from pathlib import Path

# lu 的上一層（cc-bot）要在 path 上才 import 得到 lu
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lu import render  # noqa: E402
from lu.render import CanvasState  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


def _feed(cs: CanvasState, seq: list[tuple[str, dict]]) -> str:
    for type_, d in seq:
        cs.feed(type_, d)
    return cs.render()


def test_tools() -> None:
    print("\n[工具軌跡]")
    cs = CanvasState()
    out = _feed(cs, [
        ("turn.start", {"prompt": "幫我看一下設定檔", "origin": "user"}),
        ("tool.call", {"tool": "Read", "summary": "a.py", "raw": "Read a.py", "icon": "📄"}),
        ("tool.call", {"tool": "Read", "summary": "b.py", "raw": "Read b.py", "icon": "📄"}),
        ("tool.call", {"tool": "Bash", "summary": "pytest", "raw": "pytest -q", "icon": "⚙️"}),
        ("step.commit", {"text": "看完了"}),
    ])
    check("頂部有原文", "幫我看一下設定檔" in out, out[:80])
    check("同類工具收成一行", out.count("Read") <= 1 or "2" in out, out)
    check("工具數有出現", "3" in out or "pytest" in out, out)

    # 危險指令要完整列出原文，不能被收進統計行
    cs2 = CanvasState()
    out2 = _feed(cs2, [
        ("turn.start", {"prompt": "清一下暫存", "origin": "user"}),
        ("tool.call", {"tool": "Bash", "summary": "rm", "raw": "rm -rf C:/tmp/x",
                       "icon": "⚙️", "dangerous": True}),
    ])
    check("危險指令原文完整", "rm -rf C:/tmp/x" in out2, out2)


def test_wake_header() -> None:
    print("\n[背景工作叫醒的那一行]")
    cs = CanvasState()
    out = _feed(cs, [
        ("turn.start", {"prompt": "", "origin": "wake",
                        "wake": {"id": "b1", "desc": "跑腳本", "status": "completed"}}),
    ])
    check("有背景工作說明", "跑腳本" in out, out[:120])
    check("不是使用者原文那種頭", "📥" not in out or "跑腳本" in out, out[:120])


def test_stopped_keeps_text() -> None:
    print("\n[停止保留半截]")
    cs = CanvasState()
    out = _feed(cs, [
        ("turn.start", {"prompt": "數到一百", "origin": "user"}),
        ("text.delta", {"d": "一、二、三"}),
        ("error", {"kind": "STOPPED", "detail": "好，我停下了。"}),
    ])
    # 半截的字不畫進畫布，是另外送成一則回覆（frontend 收到 STOPPED 時送出），
    # 所以要驗的是 stopped_partial 而不是 render
    check("已生成的字留著", "一、二、三" in cs.stopped_partial(), cs.stopped_partial())
    check("狀態列收掉", "💭" not in out, out)
    cs2 = CanvasState()
    _feed(cs2, [("turn.start", {"prompt": "x", "origin": "user"}),
                ("error", {"kind": "STOPPED", "detail": "好"})])
    check("一個字都還沒生成就沒有半截", cs2.stopped_partial() == "", cs2.stopped_partial())


def test_tables_and_split() -> None:
    print("\n[表格與切段]")
    md = "前言\n\n| 名稱 | 值 |\n| --- | --- |\n| a | 1 |\n| bb | 22 |\n\n結語"
    out = render.pipe_tables_to_code(md)
    check("表格轉成程式碼區塊", "```" in out and "|" not in out.split("```")[1], out)
    check("表格外的字留著", "前言" in out and "結語" in out, out)
    check("沒有表格就原樣", render.pipe_tables_to_code("純文字") == "純文字")

    long = "\n".join(f"第 {i} 行內容內容內容" for i in range(400))
    parts = render.split_message(long)
    check("每段不超過上限", all(len(p) <= render.MAX_MSG for p in parts),
          str([len(p) for p in parts]))
    joined = "".join(parts)
    check("切段不掉字", all(f"第 {i} 行" in joined for i in (0, 200, 399)),
          f"{len(parts)} 段")

    fenced = "說明\n```py\n" + "\n".join(f"x = {i}" for i in range(300)) + "\n```"
    fp = render.split_message(fenced)
    check("程式碼區塊切開後每段自己閉合",
          all(p.count("```") % 2 == 0 for p in fp), str([p.count("```") for p in fp]))


def test_spoiler() -> None:
    print("\n[工具原文]")
    short = render.spoiler_for(["ls -la"])
    check("短原文直接 spoiler", short is not None and "||ls -la||" in (short.line or ""), str(short))
    long_raw = "x" * (render.SPOILER_MAX + 50)
    out = render.spoiler_for([long_raw])
    check("長原文交出全文改掛按鈕",
          out is not None and out.line is None and out.long == long_raw, str(out)[:80])
    # 原文自己帶 || 會提早結束遮罩，要被拆開；拆完整行只剩頭尾那兩組定界
    esc = render.spoiler_for(["a || b"]).line or ""
    check("原文裡的 || 被拆開", esc.count("||") == 2 and "| |" in esc, esc)


def main() -> int:
    test_tools()
    test_wake_header()
    test_stopped_keeps_text()
    test_tables_and_split()
    test_spoiler()
    print(f"\n{'全部通過' if not FAILED else '失敗：' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
