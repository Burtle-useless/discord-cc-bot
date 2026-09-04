"""工具呼叫的參數萃取。

cc-bot 的 _fmt_tool 直接組 Discord Markdown 字串；這裡改成回 dict，
由手機端決定怎麼畫。兩個實質差異：
  1. `raw` **不截斷**——Discord 有 2000 字上限才要截，手機沒有。而核對指令原文
     正是安全防線本身，截尾就是把破壞性尾段推出視野。
  2. 不做反引號逃逸——那是 Discord Markdown 的需求，JSON 傳輸不需要。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .safety import DESTRUCTIVE_RE

ICONS: dict[str, str] = {
    "Read": "📖", "Write": "✏️", "Edit": "✏️", "MultiEdit": "✏️",
    "Bash": "⚙️", "PowerShell": "⚙️", "Glob": "🔍", "Grep": "🔍",
    "WebSearch": "🌐", "WebFetch": "🌐", "TodoWrite": "📝",
    "Agent": "🤖", "Task": "📋", "ToolSearch": "🛠️",
    "TaskCreate": "📋", "TaskUpdate": "📋", "TaskList": "📋",
    "AskUserQuestion": "❓",
}


def _summarize(name: str, inp: dict) -> str:
    """一行摘要，給摺疊列用。"""
    if name in ("Write", "Edit", "MultiEdit") and "file_path" in inp:
        return str(inp["file_path"])                    # 改檔：完整路徑，看清動的是哪個檔
    if name in ("Read", "Glob") and "file_path" in inp:
        return Path(str(inp["file_path"])).name
    if name in ("Bash", "PowerShell") and "command" in inp:
        # 優先顯示 CC 對這條指令附的意圖說明（description）——「這一步在做什麼」
        # 因此有確定性保底，不依賴 CC 在訊息裡開金口；原始指令縮短附後供核對。
        cmd = str(inp["command"]).replace("\n", " ")
        desc = str(inp.get("description") or "").strip().replace("\n", " ")
        return f"{desc[:80]} · {cmd[:80]}" if desc else cmd[:120]
    if name == "Grep" and "pattern" in inp:
        return str(inp["pattern"])[:50]
    if name == "WebSearch" and "query" in inp:
        return str(inp["query"])[:60]
    return str(list(inp.values())[0])[:60] if inp else ""


def _nlines(s: Any) -> int:
    """字串行數（空字串算 0）。增刪行數以參數行數近似，與 cc-bot 同法。"""
    return (str(s).count("\n") + 1) if s else 0


def tool_info(name: str, inp: dict) -> dict[str, Any]:
    """把一次工具呼叫萃取成事件 payload。

    `dangerous` 用與確認按鈕同一套 DESTRUCTIVE_RE 判準，且**不受確認總開關影響**——
    開關關掉時仍要在 UI 上標出危險，只是不擋。
    先前 cc-bot 把 Bash/PowerShell/Write/Edit 一律掛警示，結果每行都警示＝沒有警示。

    `kind` 是前端統計摺疊用的分類（讀檔／改檔／指令／上網），
    `added`/`removed` 是增刪行數近似（Write＝全新增、Edit＝new 減 old）。
    """
    inp = inp if isinstance(inp, dict) else {}
    dangerous = (
        name in ("Bash", "PowerShell")
        and bool(DESTRUCTIVE_RE.search(str(inp.get("command", ""))))
    )
    kind = {
        "Read": "read", "Glob": "search", "Grep": "search",
        "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
        "Bash": "cmd", "PowerShell": "cmd",
        "WebSearch": "web", "WebFetch": "web",
    }.get(name, "other")
    added = removed = 0
    if name == "Write":
        added = _nlines(inp.get("content"))
    elif name == "Edit":
        added = _nlines(inp.get("new_string"))
        removed = _nlines(inp.get("old_string"))
    return {
        "tool": name,
        "icon": ICONS.get(name, "🔧"),
        "summary": _summarize(name, inp),
        "raw": str(inp.get("command", "")),   # 完整原文，不截斷
        "dangerous": dangerous,
        "kind": kind,
        "added": added,
        "removed": removed,
        # 新增檔案顯示檔名（cc-bot 統計行的「新增 spec_lookup.py」）
        "file": Path(str(inp.get("file_path", ""))).name if name == "Write" else "",
    }
