"""`/drive on|off`：開車模式（語音回覆）的開關與模型生命週期。

**實作在 `lu.voice`**，這裡只有指令本身。語音輸入（轉錄）永遠可用、不看這個開關，
它接在訊息收取那條路上（`lu.intake`）；這個指令管的是「回覆要不要唸出來」，
那才需要載 F5-TTS、吃顯示卡記憶體。

`drive_core.py` 被移掉時整組降級成純文字，指令回「語音模組未安裝」而不是炸。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from .. import voice
from ..i18n import t

if TYPE_CHECKING:
    from ..bootstrap import BotContext

log = logging.getLogger(__name__)


# ── 指令 ────────────────────────────────────────────────────────────────
def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth
    voice.setup(ctx.settings.data_dir)

    @tree.command(name="drive", description=t("cmd_drive_desc"))
    @app_commands.choices(mode=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ])
    async def cmd_drive(inter: discord.Interaction, mode: str) -> None:
        # 會吃掉整張顯卡的東西，限主帳號開關
        if not await auth.check(inter, owner_only=True):
            return
        if not voice.available():
            await inter.response.send_message(t("drive_unavailable"), ephemeral=True)
            return
        if mode == "on":
            voice.set_on(True)
            # F5 載入很久（首次還要下載約 1.8GB），先即時回一則避免互動三秒逾時
            await inter.response.send_message(t("drive_on_loading"))
            try:
                await asyncio.to_thread(voice.drive_core.get_f5tts)
            except Exception as e:  # noqa: BLE001
                # TTS 起不來：語音輸入還是能用，只是不能語音回覆，所以開關維持開著
                log.warning("F5-TTS 載入失敗：%r", e)
                await inter.followup.send(t("drive_xtts_fail", ex=e))
                return
            await inter.followup.send(t("drive_on_ready"))
            return
        voice.set_on(False)
        # 卸載要跑 gc 與 empty_cache，可能超過三秒，先 defer
        await inter.response.defer()
        # 只卸 F5（語音回覆）；語音輸入的 Whisper 不在這裡動
        await asyncio.to_thread(voice.drive_core.unload_f5tts)
        await inter.followup.send(t("drive_off"))
