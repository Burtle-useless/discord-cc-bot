"""引擎層：與前端無關的 Claude Code 驅動核心。

**分層鐵律**：這個 package 底下禁止 import 任何傳輸層或 UI 套件
（fastapi / sse_starlette / discord / ...）。對外只能透過 protocol.Frontend 溝通。
檢驗標準：用一個 print-to-console 的假 Frontend 就能在終端機跑完整回合
（見 server/dev_console.py）。
"""
from .fold import NO_RESPONSE, clean_reply, fold_messages, think_digest
from .runner import TurnResult, run_turn
from .safety import DESTRUCTIVE_RE, needs_confirm
from .state import ConvState, eff_effort, eff_model, get_state, persist
from .toolinfo import tool_info

__all__ = [
    "NO_RESPONSE", "clean_reply", "fold_messages", "think_digest",
    "TurnResult", "run_turn",
    "DESTRUCTIVE_RE", "needs_confirm",
    "ConvState", "eff_effort", "eff_model", "get_state", "persist",
    "tool_info",
]
