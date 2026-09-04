"""engine 對外需要的能力，收成一個 `Ports` 一次接上。

engine 不認識 HTTP、SSE、推播——它只知道「資料變了要通知某人」「要跟手機要位置」
「有檔案要給他」「助理自己醒來的週期要排隊」。這些動作各模組原本各留一個
`set_*` 掛勾，由 transport/app.py 一條一條接線；接線散在五處，漏接任何一條都是
**靜默不動**（鬧鐘不響、位置永遠拿不到、檔案登記了沒人通知）。

這裡把它們列成一個 frozen dataclass 的**必填欄位**：少給一個，建 `Ports` 時就
TypeError 炸在啟動，而不是上線後才發現某條路沒接。`install` 是唯一的接線入口，
內部仍呼叫各模組既有的 `set_*`（單元測試還在直接用它們，維持不動）。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from . import agenda_tools, bg_notify, file_tools, kanban_tools, location


@dataclass(frozen=True, slots=True)
class Ports:
    """engine 需要的外部能力。欄位一律必填，缺一個就在建構時炸。"""

    # 助理自己開了一個模型週期（背景工作跑完後那一輪）：排進該對話的佇列。
    waker: bg_notify.Waker
    # 跟手機要一份即時位置；拿不到回 None。
    locate: Callable[[], Awaitable[dict[str, Any] | None]]
    # 助理登記了一個要給手機的檔案：通知所有裝置。
    offer: Callable[[dict[str, Any]], Awaitable[None]]
    # 行事曆／鬧鐘／記帳／課表變了（參數是哪一類）：通知手機重拉。
    agenda_changed: Callable[[str], Awaitable[None]]
    # 看板變了：通知手機重拉。
    kanban_changed: Callable[[str], Awaitable[None]]


def install(ports: Ports) -> None:
    """把 `Ports` 接到 engine 各模組。整個服務只該呼叫一次（transport/app.py）。"""
    bg_notify.set_waker(ports.waker)
    location.set_locate_hook(ports.locate)
    file_tools.set_offer_hook(ports.offer)
    agenda_tools.set_change_hook(ports.agenda_changed)
    kanban_tools.set_change_hook(ports.kanban_changed)
