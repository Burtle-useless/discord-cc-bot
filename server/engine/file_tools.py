"""給助理的傳檔工具（in-process MCP）：把電腦上的檔案送到手機。

反方向（手機傳上來）走的是 HTTP 上傳，不需要工具——那是使用者主動的動作。
這一邊必須是工具，因為發起者是模型：它做完一張圖、一份報告之後，
要能自己決定「這個他會想拿到手機上」。

工具本身不搬檔案也不管網路，只在 outbox 登記一筆並敲一下掛勾，
真正的下載端點在 transport 那一層。
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

import outbox

# 登記完要通知手機（發推播、刷新清單）。engine 不該認識 transport，
# 一樣只留掛勾，由 app.py 啟動時注入。
_on_offer: Callable[[dict[str, Any]], Awaitable[None]] | None = None


def set_offer_hook(fn: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    global _on_offer
    _on_offer = fn


SERVER_NAME = "files"

_DESC = (
    "把這台電腦上的一個檔案傳到他手機上。做好的圖、報告、匯出的資料想給他，就用這個。"
    "他會收到通知，檔案存在 App 的「助理傳來的檔案」裡等他下載。"
    "只傳單一檔案；整個資料夾請先壓成 zip 再傳。"
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "檔案在這台電腦上的完整絕對路徑"},
        "note": {"type": "string", "description": "這是什麼，一句話，可省略"},
    },
    "required": ["path"],
}


def make_send_file(conv_id: str):
    """建一支綁定 [conv_id] 的 send_file。單獨抽出來是為了讓測試拿得到 handler。"""

    @tool("send_file", _DESC, _SCHEMA)
    async def send_file(args: dict[str, Any]) -> dict[str, Any]:
        try:
            item = outbox.offer(args["path"], args.get("note", ""), conv_id)
        except outbox.OutboxError as e:
            return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
        if _on_offer is not None:
            await _on_offer(item)
        return {"content": [{"type": "text", "text": json.dumps(
            {"sent": item["name"], "bytes": item["bytes"]}, ensure_ascii=False)}]}

    return send_file


def server_for(conv_id: str):
    """幫一個對話建它專屬的傳檔工具。

    MCP 呼叫本身是無狀態的，工具沒有「我是誰叫的」這個概念。但檔案卡片必須落在
    **發起它的那個對話**裡：同時開著工作對話與助理的對話時，工作那邊做出來的報告
    會掉進使用者當下正在看的那一頁，看起來就像助理無緣無故丟了一份不相干的檔案。

    所以用 closure 把 conv_id 綁進去，一個對話一個 server 實例。實例只是一張
    in-process 的工具註冊表，很輕，而且只在建立長駐 client 時取一次
    （client_sig 不含 mcp_servers，換實例不會觸發重建）。
    """
    return create_sdk_mcp_server(
        name=SERVER_NAME, version="1.0.0", tools=[make_send_file(conv_id)],
    )
