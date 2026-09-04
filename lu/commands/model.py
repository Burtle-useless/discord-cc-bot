"""/model 面板與 /effort 捷徑。

面板是 ephemeral 的，三個 select：模型、思考強度、套用範圍（這個對話／帳號預設）。
資料全部來自 `engine.models`（官方清單，CLI 升版自己長出新模型），生效值打勾；
選了模型之後思考強度只列該模型的 `supportedEffortLevels`。選了就立刻套用並存檔，
沒有「套用」按鈕——少一步。

套用之後把這條對話的長駐 client 丟掉（規格要求），下一回合以新設定重建；
回合進行中不丟（會把正在跑的那輪殺掉）——生效值本來就在 client 指紋裡，
回合結束後下一次 acquire 自然重建。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from engine import client_pool
from engine import models
from engine import state as state_mod
from engine.state import eff_effort, eff_model, get_state

from ..i18n import t
from ..profile import conv_id as conv_of

if TYPE_CHECKING:
    from ..bootstrap import BotContext

FOLLOW = "__follow__"      # 這個對話跟著帳號預設
DEFAULT_EFFORT = "__default__"


class ModelPanel(discord.ui.View):
    """一個人的 ephemeral 面板。"""

    def __init__(self, ctx: "BotContext", conv: str, user_id: int) -> None:
        super().__init__(timeout=600)
        self.ctx = ctx
        self.conv = conv
        self.user_id = user_id
        self.scope = "conv"
        self.notice = ""
        self._rebuild()

    # ── 資料 ──
    def _current(self) -> tuple[str | None, str | None]:
        """目前這個範圍存的值（None＝沒設）。"""
        if self.scope == "conv":
            st = get_state(self.conv)
            return st.model, st.effort
        return state_mod.default_model, state_mod.default_effort

    def text(self) -> str:
        st = get_state(self.conv)
        lines = [
            t("model_panel_title"),
            t("model_panel_effective", model=eff_model(st), effort=eff_effort(st) or t("default_inline")),
            t("model_panel_scope_line", scope=t("model_panel_scope_conv") if self.scope == "conv"
              else t("model_panel_scope_account")),
            t("model_panel_conv_override") if (st.model or st.effort) else t("model_panel_follow"),
        ]
        m = models.find(eff_model(st))
        if m is not None and not m.supports_effort:
            lines.append(t("model_panel_effort_na"))
        if self.notice:
            lines.append(self.notice)
        lines.append(t("model_panel_source", src=models.source()))
        return "\n".join(lines)

    def _rebuild(self) -> None:
        self.clear_items()
        cur_model, cur_effort = self._current()
        # 範圍
        scope = discord.ui.Select(placeholder=t("model_panel_pick_scope"), options=[
            discord.SelectOption(label=t("model_panel_scope_conv"), value="conv", default=self.scope == "conv"),
            discord.SelectOption(label=t("model_panel_scope_account"), value="account",
                                 default=self.scope == "account"),
        ])
        scope.callback = self._on_scope
        self.add_item(scope)
        # 模型
        opts: list[discord.SelectOption] = []
        if self.scope == "conv":
            opts.append(discord.SelectOption(label=t("model_follow_default"), value=FOLLOW,
                                             default=cur_model is None))
        for m in models.catalog()[:24]:
            opts.append(discord.SelectOption(
                label=m.name[:100], value=m.value[:100],
                description=(m.description or m.resolved or None) and (m.description or m.resolved)[:100],
                default=(cur_model == m.value or (cur_model is not None and cur_model == m.resolved)),
            ))
        if cur_model and not any(o.value == cur_model for o in opts):
            # 舊資料存的完整 id 不在官方清單上：照樣列出來，不然打勾的東西不見了
            opts.append(discord.SelectOption(label=cur_model[:100], value=cur_model[:100], default=True))
        model_sel = discord.ui.Select(placeholder=t("model_panel_pick_model"), options=opts[:25])
        model_sel.callback = self._on_model
        self.add_item(model_sel)
        # 思考強度：依生效模型列
        st = get_state(self.conv)
        basis = cur_model or (eff_model(st) if self.scope == "conv" else None)
        levels = models.efforts_for(basis)
        eopts = [discord.SelectOption(label=t("effort_default_opt"), value=DEFAULT_EFFORT,
                                      default=cur_effort is None)]
        for lv in levels:
            eopts.append(discord.SelectOption(label=lv, value=lv, default=cur_effort == lv))
        if cur_effort and cur_effort not in levels:
            eopts.append(discord.SelectOption(label=cur_effort, value=cur_effort, default=True))
        effort_sel = discord.ui.Select(placeholder=t("model_panel_pick_effort"), options=eopts[:25],
                                       disabled=not levels)
        effort_sel.callback = self._on_effort
        self.add_item(effort_sel)

    # ── 互動 ──
    async def interaction_check(self, inter: discord.Interaction) -> bool:
        return inter.user.id == self.user_id

    async def _refresh(self, inter: discord.Interaction) -> None:
        self._rebuild()
        await inter.response.edit_message(content=self.text(), view=self)

    async def _on_scope(self, inter: discord.Interaction) -> None:
        sel = inter.data.get("values") if inter.data else None
        self.scope = (sel or ["conv"])[0]
        self.notice = ""
        await self._refresh(inter)

    async def _on_model(self, inter: discord.Interaction) -> None:
        sel = (inter.data.get("values") if inter.data else None) or []
        if not sel:
            await self._refresh(inter)
            return
        value = sel[0]
        if value != FOLLOW and not models.is_known(value):
            self.notice = t("model_unknown", model=value)
            await self._refresh(inter)
            return
        await self._apply(model=None if value == FOLLOW else value, effort=..., inter=inter)

    async def _on_effort(self, inter: discord.Interaction) -> None:
        sel = (inter.data.get("values") if inter.data else None) or []
        if not sel:
            await self._refresh(inter)
            return
        value = sel[0]
        await self._apply(model=..., effort=None if value == DEFAULT_EFFORT else value, inter=inter)

    async def _apply(self, *, model: str | None | type(Ellipsis), effort: str | None | type(Ellipsis),
                     inter: discord.Interaction) -> None:
        """套用到目前範圍並存檔。`...` 代表這一項不動。"""
        if self.scope == "conv":
            st = get_state(self.conv)
            if model is not ...:
                st.model = model
            if effort is not ...:
                st.effort = effort
            state_mod.persist(st)
            scope_label = t("model_panel_scope_conv")
        else:
            cur_m, cur_e = state_mod.default_model, state_mod.default_effort
            state_mod.save_defaults(
                cur_m if model is ... else model,
                cur_e if effort is ... else effort,
            )
            scope_label = t("model_panel_scope_account")
        if not self.ctx.worker.is_running(self.conv):
            await client_pool.drop(self.conv)
        st = get_state(self.conv)
        self.notice = t("model_panel_applied", scope=scope_label, model=eff_model(st),
                        effort=eff_effort(st) or t("default_inline"))
        await self._refresh(inter)


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth

    @tree.command(name="model", description=t("cmd_model_desc"))
    async def cmd_model(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        panel = ModelPanel(ctx, conv_of(inter.channel_id), inter.user.id)
        await inter.response.send_message(panel.text(), view=panel, ephemeral=True)

    @tree.command(name="effort", description=t("cmd_effort_desc"))
    @app_commands.choices(effort=[
        *[app_commands.Choice(name=lv, value=lv) for lv in models.ALL_EFFORTS],
        app_commands.Choice(name=t("effort_default_opt"), value=DEFAULT_EFFORT),
    ])
    async def cmd_effort(inter: discord.Interaction, effort: str) -> None:
        if not await auth.check(inter):
            return
        value = None if effort == DEFAULT_EFFORT else effort
        state_mod.save_defaults(state_mod.default_model, value)
        conv = conv_of(inter.channel_id)
        if not ctx.worker.is_running(conv):
            await client_pool.drop(conv)
        await inter.response.send_message(t("effort_set", effort=value or t("default_inline")))
