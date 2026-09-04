"""跟這台電腦有關的指令：/usage、/screenshot、/plan。

用量與截圖直接用 `server/` 的 engine／transport 模組，陸這邊只負責畫成 Discord 訊息：
  engine.plan_usage  訂閱方案的額度（5 小時／7 天視窗），走官方 API
  engine.local_usage 逐字稿算出來的本機用量（今日／本月／逐日）
  transport.tools.screenshot  抓螢幕

`/plan` 只剩唯讀顯示：context 上限現在由引擎每回合向 SDK 問權威值
（`ConvState.ctx_max`／`ctx_threshold`），不再靠使用者手動設方案來猜。
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from ..i18n import t

if TYPE_CHECKING:
    from ..bootstrap import BotContext

log = logging.getLogger(__name__)


def _bar(pct: float, w: int = 14) -> str:
    n = max(0, min(w, int(round(pct / 100 * w))))
    return "█" * n + "░" * (w - n)


def _fmt_k(n: int | float) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth

    @tree.command(name="usage", description=t("cmd_usage_desc"))
    async def cmd_usage(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        # 兩份資料都可能要跑幾秒（官方 API 一趟往返、逐字稿首掃十幾秒），
        # 一律 defer 再慢慢算，不然 Discord 三秒就判互動失敗
        await inter.response.defer()
        from engine import local_usage, plan_usage
        limits, rep = await asyncio.gather(
            asyncio.to_thread(plan_usage.limits),
            asyncio.to_thread(local_usage.report),
        )
        lines = [t("usage_title")]
        if limits:
            for lim in limits:
                pct = float(lim.get("pct") or 0)
                label = str(lim.get("label") or lim.get("key") or "")
                reset = lim.get("resets_at")
                tail = t("usage_resets", ts=int(reset)) if reset else ""
                lines.append(f"{label}　`{_bar(pct)}` {pct:.0f}%{tail}")
        else:
            lines.append(t("usage_plan_unavailable"))
        today = rep.get("today") or {}
        month = rep.get("month") or {}
        lines.append(t("usage_local",
                       today_in=_fmt_k(today.get("input", 0)),
                       today_out=_fmt_k(today.get("output", 0)),
                       month_in=_fmt_k(month.get("input", 0)),
                       month_out=_fmt_k(month.get("output", 0))))
        cost = month.get("cost")
        if cost:
            lines.append(t("usage_cost", cost=f"{float(cost):.2f}"))
        await inter.followup.send("\n".join(lines))

    @tree.command(name="screenshot", description=t("cmd_screenshot_desc"))
    async def cmd_screenshot(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        await inter.response.defer()
        try:
            from transport.tools import screenshot
            png = await screenshot(all_screens=True)
        except Exception as e:  # noqa: BLE001
            log.exception("截圖失敗")
            await inter.followup.send(t("screenshot_failed", err=e))
            return
        await inter.followup.send(file=discord.File(io.BytesIO(png), filename="screen.png"))

    @tree.command(name="plan", description=t("cmd_plan_desc"))
    async def cmd_plan(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        from engine.state import get_state
        from engine.turn import ctx_limit
        from ..profile import conv_id as conv_of
        st = get_state(conv_of(inter.channel_id))
        # ctx_max 是向 SDK 問到的權威值，0＝這條對話還沒跑過任何回合
        src = t("plan_src_sdk") if st.ctx_max else t("plan_src_guess")
        await inter.response.send_message(
            t("plan_info", limit=f"{ctx_limit(st):,}", src=src), ephemeral=True)
