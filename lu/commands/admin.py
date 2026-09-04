"""管理指令：addchannel／removechannel／adduser／removeuser／listusers／confirm。"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

import config

from ..i18n import t

if TYPE_CHECKING:
    from ..bootstrap import BotContext


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth

    @tree.command(name="adduser", description=t("cmd_adduser_desc"))
    async def cmd_adduser(inter: discord.Interaction, user: discord.Member) -> None:
        if not await auth.check(inter, owner_only=True):
            return
        if user.id == auth.owner_id:
            await inter.response.send_message(t("adduser_already_owner"), ephemeral=True)
            return
        auth.add_user(user.id)
        await inter.response.send_message(t("adduser_done", mention=user.mention, id=user.id))

    @tree.command(name="removeuser", description=t("cmd_removeuser_desc"))
    async def cmd_removeuser(inter: discord.Interaction, user: discord.Member) -> None:
        if not await auth.check(inter, owner_only=True):
            return
        if user.id == auth.owner_id:
            await inter.response.send_message(t("removeuser_cant_owner"), ephemeral=True)
            return
        auth.remove_user(user.id)
        await inter.response.send_message(t("removeuser_done", mention=user.mention, id=user.id))

    @tree.command(name="listusers", description=t("cmd_listusers_desc"))
    async def cmd_listusers(inter: discord.Interaction) -> None:
        if not await auth.check(inter, owner_only=True):
            return
        lines = [f"• `{uid}`{t('owner_tag') if uid == auth.owner_id else ''}" for uid in sorted(auth.users)]
        await inter.response.send_message(t("listusers_header") + "\n".join(lines), ephemeral=True)

    @tree.command(name="addchannel", description=t("cmd_addchannel_desc"))
    async def cmd_addchannel(inter: discord.Interaction) -> None:
        # 這個指令不能走 auth.check（新頻道還沒在清單裡），直接驗主帳號
        if inter.user.id != auth.owner_id:
            await inter.response.send_message(t("owner_only"), ephemeral=True)
            return
        if auth.channel_ok(inter.channel_id):
            await inter.response.send_message(t("addchannel_already"), ephemeral=True)
            return
        auth.add_channel(inter.channel_id)
        await inter.response.send_message(t("addchannel_done"))

    @tree.command(name="removechannel", description=t("cmd_removechannel_desc"))
    async def cmd_removechannel(inter: discord.Interaction) -> None:
        if not await auth.check(inter, owner_only=True):
            return
        auth.remove_channel(inter.channel_id)
        await inter.response.send_message(t("removechannel_done"))

    @tree.command(name="confirm", description=t("cmd_confirm_desc"))
    @app_commands.choices(switch=[
        app_commands.Choice(name=t("confirm_switch_on"), value="on"),
        app_commands.Choice(name=t("confirm_switch_off"), value="off"),
    ])
    async def cmd_confirm(inter: discord.Interaction, switch: str) -> None:
        if not await auth.check(inter):
            return
        # engine.safety.needs_confirm 每次呼叫都讀 config.CONFIRM_ENABLED，改模組屬性即生效
        # （Final 只是型別提示）。不落檔：跟舊 cc-bot 一樣重啟回到 .env 的預設。
        config.CONFIRM_ENABLED = switch == "on"  # type: ignore[misc]
        await inter.response.send_message(
            t("confirm_toggle_on") if config.CONFIRM_ENABLED else t("confirm_toggle_off"))
