"""給助理用的看板工具（in-process MCP）。

看板是「階段分欄、卡片在欄底下」，所以工具的粒度就是：新增一件工作、
把工作換到別的階段。屬於哪個企劃寫在標題裡，不是獨立欄位。

刻意**沒有**刪除工具：助理不該自己決定哪件工作不用做了。要拿掉由使用者
在 App 上封存。
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

import kanban

_on_change: Callable[[str], Awaitable[None]] | None = None


def set_change_hook(fn: Callable[[str], Awaitable[None]]) -> None:
    global _on_change
    _on_change = fn


async def _changed() -> None:
    if _on_change is not None:
        await _on_change("kanban")


def _ok(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False)}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


@tool(
    "kanban_list",
    "看使用者的工作看板：待辦、進行中、完成各有哪些工作，哪些是急的。"
    "要知道他手上在跑什麼、或是要動看板之前，先用這個。",
    _schema({}, []),
)
async def kanban_list(args: dict[str, Any]) -> dict[str, Any]:
    board = kanban.get_board()
    lines: list[str] = []
    for col in board.get("columns", []):
        if not col["total"]:
            continue
        more = f"・另有 {col['hidden']} 張收起來" if col["hidden"] else ""
        lines.append(f"■ {col['label']}（{col['total']}{more}）")
        for c in col["cards"]:
            flag = "急 " if c.get("urgent") else ""
            note = (c.get("note") or "").strip()
            brief = f" — {note[:40]}{'…' if len(note) > 40 else ''}" if note else ""
            lines.append(f"    [{c['id']}] {flag}{c['title']}{brief}")
    if not lines:
        return _ok("看板是空的。")
    return _ok("\n".join(lines))


@tool(
    "kanban_add",
    "在看板上新增一件工作。標題要帶得出是哪個企劃的，像「助理－看板拖放」"
    "或「論文－補 EIS 圖」，這樣他一眼掃得出來。",
    _schema({
        "title": {"type": "string",
                  "description": "這件工作要做什麼。前面帶企劃名，用「－」隔開"},
        "status": {"type": "string", "enum": ["todo", "doing", "done"],
                   "description": "放進哪一欄，預設 todo"},
        "urgent": {"type": "boolean", "description": "是不是急件，預設否"},
        "note": {"type": "string", "description": "補充說明，可省略"},
    }, ["title"]),
)
async def kanban_add(args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("title") or "").strip()
    if not title:
        return _err("title 不能是空的")
    card = kanban.add_card(
        title=title,
        status=args.get("status", "todo"),
        urgent=bool(args.get("urgent", False)),
        note=args.get("note", ""),
    )
    await _changed()
    return _ok({"id": card["id"], "title": card["title"], "status": card["status"]})


@tool(
    "kanban_update",
    "推進或修改一件工作：換欄（todo/doing/done）、標成急件、改標題或說明。"
    "開始做就換到 doing，做完換到 done。",
    _schema({
        "card_id": {"type": "string", "description": "工作 id，用 kanban_list 查"},
        "status": {"type": "string", "enum": ["todo", "doing", "done"]},
        "urgent": {"type": "boolean"},
        "title": {"type": "string"},
        "note": {"type": "string", "description": "目前進度或卡在哪"},
    }, ["card_id"]),
)
async def kanban_update(args: dict[str, Any]) -> dict[str, Any]:
    card = kanban.update_card(
        args.get("card_id", ""),
        title=args.get("title"),
        status=args.get("status"),
        urgent=args.get("urgent"),
        note=args.get("note"),
    )
    if card is None:
        return _err("找不到這張卡片，先用 kanban_list 確認 id")
    await _changed()
    return _ok({
        "id": card["id"], "title": card["title"],
        "status": card["status"], "urgent": card["urgent"],
    })


ALL_TOOLS = [kanban_list, kanban_add, kanban_update]
SERVER_NAME = "kanban"
SERVER = create_sdk_mcp_server(
    name=SERVER_NAME, version="1.0.0", tools=ALL_TOOLS,
)
