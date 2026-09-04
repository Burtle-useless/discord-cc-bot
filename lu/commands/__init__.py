"""slash 指令：各模組自己 `register(tree, ctx)`，這裡只負責一次全掛上去。

指令分六組：core（help／guide／stop／status／continue／rename／cd／pwd）、
model（/model 面板與 /effort 捷徑）、sessions（接管既有 session）、
memory（search／recall／handoff）、tools（usage／screenshot／plan）、
admin（白名單與 /confirm）。

排程、worktree、語音三組是選配：模組不在（或 import 失敗）就整組不掛，
不能讓 bot 起不來——那三個都依賴外部套件（croniter、git、faster-whisper）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from discord import app_commands

if TYPE_CHECKING:
    from ..bootstrap import BotContext


def register_all(tree: app_commands.CommandTree, ctx: "BotContext") -> int:
    """把所有指令掛到 tree 上，回傳掛了幾個。"""
    import logging
    log = logging.getLogger(__name__)
    from . import admin, core, memory, model, sessions, tools
    before = len(tree.get_commands())
    core.register(tree, ctx)
    model.register(tree, ctx)
    sessions.register(tree, ctx)
    memory.register(tree, ctx)
    tools.register(tree, ctx)
    admin.register(tree, ctx)
    # 選配的三組：少一個套件不該讓整隻 bot 起不來，掛不上就記一筆繼續
    for name in ("schedule", "wt", "drive"):
        try:
            mod = __import__(f"{__name__}.{name}", fromlist=["register"])
            mod.register(tree, ctx)
        except Exception as e:  # noqa: BLE001
            log.warning("選配指令 %s 沒掛上：%r", name, e)
    return len(tree.get_commands()) - before
