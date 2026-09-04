"""Frontend Protocol：引擎對外的唯一介面。

`engine/` 底下禁止 import 任何傳輸層或 UI 套件，只能透過這兩個方法對外溝通。
檢驗標準：能用一個 print-to-console 的假 Frontend 在終端機跑完整回合。

cc-bot 原本有兩套獨立的 Discord View——破壞性指令確認與 AskUserQuestion 選項，
加起來約 150 行。但兩者語意完全相同（顯示選項、等一個答案、可能逾時），
差別只在選項內容和要不要秀指令原文。合併成一個 `ask` 原語後剩約 30 行，
手機端也只要做一個 modal 元件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, Sequence, runtime_checkable

from .events import Event

AskKind = Literal[
    "confirm_destructive",  # 破壞性指令確認，raw 必須帶指令原文
    "choose",               # 選項題（[[ASK:]] 標記抽出來的，見 engine.fold）
]


@dataclass(frozen=True, slots=True)
class AskChoice:
    """一個選項。confirm_destructive 固定用 yes / no 兩個 id。"""

    id: str
    label: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AskRequest:
    """向使用者提問。"""

    kind: AskKind
    title: str
    body: str = ""                      # 供人閱讀的說明
    raw: str = ""                       # 指令原文全文，**禁止摘要或截斷**
    choices: Sequence[AskChoice] = ()
    timeout_sec: float = 300.0
    require_biometric: bool = False     # 手機端需通過生物辨識才能按下執行


@dataclass(frozen=True, slots=True)
class AskResponse:
    choice_id: str


@runtime_checkable
class Frontend(Protocol):
    """引擎唯一對外介面。"""

    async def emit(self, ev: Event) -> None:
        """單向送出一則事件。

        實作必須 non-blocking 且**永不拋例外**——前端斷線、緩衝滿、序列化失敗，
        都不可以拖垮正在跑的回合。做法是只寫進 ring buffer 就返回，
        真正的送出由獨立的 task 負責。
        """
        ...

    async def ask(self, req: AskRequest) -> AskResponse | None:
        """提問並等待答案。

        回 `None` 代表逾時。呼叫端一律 **fail-closed**：逾時與取消同樣視為拒絕，
        沿用 cc-bot `_needs_confirm` 的既有語意——判斷不出來就當危險。
        """
        ...


# ── 破壞性指令確認的標準 choices（兩處共用，避免 id 寫錯對不上）──────────────
CONFIRM_CHOICES: tuple[AskChoice, ...] = (
    AskChoice(id="no", label="取消", detail="拒絕這個指令，CC 會停手並改問你"),
    AskChoice(id="yes", label="執行", detail="放行這個指令"),
)


def is_approved(resp: AskResponse | None) -> bool:
    """把 ask 的回覆解讀成「是否放行」。逾時（None）一律不放行。"""
    return resp is not None and resp.choice_id == "yes"
