"""破壞性指令護欄。

這是黑名單，擋常見偽裝、不擋決心——真正的隔離要靠沙箱或白名單（架構級改動）。
在「人不在電腦前」的使用情境下，它是唯一一道人為把關，所以 config 的預設是開。

**但它可以整個被啟動環境關掉，而且這台機器上就是關的。** `config.CONFIRM_ENABLED`
讀環境變數 `CONFIRM_DANGEROUS`，`launch_butler.vbs` 寫死了 `=0`（使用者自己的選擇）：
關閉時 `needs_confirm` 第一行就回 False，破壞性指令**不會跳確認、直接執行**。
現在的狀態從 `/v1/settings` 的 `confirm_dangerous` 欄位看得到，不必猜。
"""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

import config
from protocol import CONFIRM_CHOICES, AskRequest, Frontend, is_approved

# 破壞性指令樣式（大小寫不敏感、以詞界比對避免 confirm 之類誤判）。
# 只攔最常見的高風險操作，命中才跳確認、其餘一律放行。
DESTRUCTIVE_RE = re.compile(
    r"(?:\brm\s+-[rf]|\brmdir\b|\brd\s+/s|\bdel\s+/|\berase\s+/|"
    r"\bremove-item\b|\bformat\s|\bmkfs\b|\bdd\s+if=|"
    r"\bgit\s+push\b|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-|\bgit\s+checkout\s+--\s|\bgit\s+branch\s+-D\b|"
    r"\bshutdown\b|\brestart-computer\b|\bstop-computer\b|\btaskkill\b|\bstop-process\b|\bstop-service\b|"
    r"\breg\s+delete\b|\bdiskpart\b|\bsc\s+delete\b|\bcipher\s+/w|"
    # 編碼／下載執行類繞過載體。仍是黑名單；bare -e 刻意不攔（grep -e 等誤殺率太高）。
    r"\biex\b|\binvoke-expression\b|-encodedcommand\b|-enc\b|\bfrombase64string\b|\bdownloadstring\b|"
    r"\bcurl\b[^|\n]*\|\s*(?:sh|bash|iex)\b|\biwr\b[^|\n]*\|\s*iex\b|\bwget\b[^|\n]*\|\s*(?:sh|bash)\b)",
    re.IGNORECASE,
)


def needs_confirm(tool_name: str, tool_input: dict) -> bool:
    """判斷這次工具呼叫是否為需確認的破壞性動作。

    只看會執行任意系統指令的 Bash/PowerShell（惡意夾帶／幻象指令的主要途徑），
    比對指令內容有沒有命中 DESTRUCTIVE_RE。

    判斷出錯時視同危險、攔下確認（**fail-closed**）：寧可多按一次，
    也不讓「弄壞判斷函式」變成解除安全鎖的後門。
    """
    if not config.CONFIRM_ENABLED:
        return False
    try:
        if tool_name in ("Bash", "PowerShell"):
            return bool(DESTRUCTIVE_RE.search(str(tool_input.get("command", ""))))
    except Exception:
        return True
    return False


def confirm_prompt(tool_name: str, tool_input: dict, mins: int) -> tuple[str, str, str]:
    """確認框要顯示的 (標題, 說明, 原文)。

    Bash/PowerShell 一律顯示**指令全文，不可截斷或摘要**——攻擊面正是
    「說明講 A、指令做 B」，一截尾就把破壞性尾段推出視野。
    """
    return (
        f"要執行這個 {tool_name} 指令嗎？",
        f"偵測到破壞性操作。{mins} 分鐘未回應將自動取消。",
        str(tool_input.get("command", "")),
    )


def deny(reason: str) -> dict:
    """組出 PreToolUse hook 的拒絕回應（deny 會讓 CC 收到 reason、不執行該工具）。"""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


ASK_DELEGATED_REASON = (
    "問題已直接送達使用者的手機，等他回覆即可。本回合到此為止，不要自問自答、"
    "不要繼續輸出，停下來等下一則訊息。"
)
DENY_CANCEL_REASON = (
    "使用者按了取消，這個指令不執行。不要改寫指令重試、不要換方法繞過，"
    "停手並說明你原本想做什麼、為什麼需要它。"
)


def make_pretool_hook(
    state: Any, frontend: Frontend
) -> Callable[[dict, str | None, object], Awaitable[dict]]:
    """為某對話產生 PreToolUse hook。

    非破壞性動作回空 dict（放行，不干擾自動化工作流）；破壞性指令走 frontend.ask
    請使用者確認，取消或逾時回 deny 擋下並告知 CC 停手。
    用 hook 而非 can_use_tool，因為後者在 headless/SDK 下不會被觸發，
    而 PreToolUse hook 即使在 bypassPermissions 下也照樣觸發、deny 能真正擋下工具。
    """

    async def _hook(input_data: dict, tool_use_id: str | None, context: object) -> dict:
        tool_name = input_data.get("tool_name", "") if isinstance(input_data, dict) else ""
        tool_input = input_data.get("tool_input", {}) if isinstance(input_data, dict) else {}
        # AskUserQuestion：headless/SDK 下 CLI 無前端可顯示，內建工具會立刻回假 error，
        # 害 CC 誤判工具失敗而搶答。改由 hook 攔下 deny，另從串流的 ToolUseBlock
        # 抓問題內容走 frontend.ask。deny 不影響 ToolUseBlock 出現在串流。
        if tool_name == "AskUserQuestion":
            return deny(ASK_DELEGATED_REASON)
        if not needs_confirm(tool_name, tool_input):
            return {}
        mins = max(1, int(config.CONFIRM_TIMEOUT_SEC // 60))
        title, body, raw = confirm_prompt(tool_name, tool_input, mins)
        resp = await frontend.ask(AskRequest(
            kind="confirm_destructive",
            title=title,
            body=body,
            raw=raw,
            choices=CONFIRM_CHOICES,
            timeout_sec=config.CONFIRM_TIMEOUT_SEC,
            require_biometric=True,
        ))
        if is_approved(resp):
            return {}
        if resp is None:
            return deny(f"使用者在 {mins} 分鐘內未回應，指令已自動取消。停手並改問使用者。")
        return deny(DENY_CANCEL_REASON)

    return _hook
