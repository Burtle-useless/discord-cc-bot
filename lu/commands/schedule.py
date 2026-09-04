"""排程指令：`/schedule <自然語言>` 建立、`/schedules` 列出與刪除。

自然語言交給 `engine.meta.ask_haiku` 解析成 cron ＋ 下一次執行時刻。prompt 一定要
把「現在幾點幾分週幾」餵進去（`scheduler.now_hint`），否則「今晚八點」這種相對時間
會被算到昨天或明天去。

**接線**：`register(tree, ctx)` 會建一個 `scheduler.Scheduler` 並掛在 `ctx.scheduler`
上，但**不會**自己啟動背景迴圈——register 是在事件迴圈跑起來之前呼叫的，那時候
`asyncio.create_task` 還沒有 loop 可用。請在 `on_ready` 裡呼叫 `ctx.scheduler.start()`。

解析失敗一律講清楚是哪一關掛掉（沒吐 JSON／cron 無效／算不出時間／時間在過去），
不要靜默吃掉——使用者才知道要怎麼改講法。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from .. import scheduler as sched_mod
from ..i18n import t

if TYPE_CHECKING:
    from ..bootstrap import BotContext

# Discord 下拉選單硬上限 25 個選項
MAX_OPTIONS = 25
# 一則訊息 2000 字，留點餘裕給標題與「還有幾筆」那行
MAX_TEXT = 1800


def _line(item: dict[str, Any]) -> str:
    return t("schedule_line",
             id=item.get("id", "?"),
             task=str(item.get("task") or "")[:80],
             next=str(item.get("next_run") or "")[:16] or t("unknown"),
             cron=str(item.get("cron") or "") or t("once"))


class _DeleteView(discord.ui.View):
    """刪除用的下拉選單。選一個就刪一個，刪完把選項從清單裡拿掉。"""

    def __init__(self, sched: sched_mod.Scheduler, auth: Any,
                 items: list[dict[str, Any]]) -> None:
        super().__init__(timeout=180)
        self.sched = sched
        self.auth = auth
        select: discord.ui.Select = discord.ui.Select(
            placeholder=t("schedule_pick"),
            options=[
                discord.SelectOption(
                    label=f"{it.get('id', '?')} — {str(it.get('task') or '')[:60]}"[:100],
                    value=str(it.get("id", "")),
                    description=(str(it.get("next_run") or "")[:16] or None),
                )
                for it in items[:MAX_OPTIONS]
            ],
        )
        select.callback = self._on_pick
        self.add_item(select)
        self._select = select

    async def _on_pick(self, inter: discord.Interaction) -> None:
        # 選單留在頻道上，誰都點得到 → 這裡要再驗一次權限（舊版的刪除按鈕沒驗）
        if not await self.auth.check(inter):
            return
        sid = self._select.values[0]
        if self.sched.remove(sid):
            await inter.response.send_message(t("schedule_deleted", id=sid), ephemeral=True)
        else:
            await inter.response.send_message(t("schedule_gone", id=sid), ephemeral=True)


def register(tree: app_commands.CommandTree, ctx: "BotContext") -> None:
    auth = ctx.auth
    sched = sched_mod.Scheduler(ctx)
    # 指令與 on_ready 都要拿到同一個實例；BotContext 沒有這個欄位，掛上去給接線用
    ctx.scheduler = sched

    @tree.command(name="schedule", description=t("cmd_schedule_desc"))
    async def cmd_schedule(inter: discord.Interaction, task: str) -> None:
        if not await auth.check(inter):
            return
        # ask_haiku 要跑一趟 CLI，三秒一定不夠
        await inter.response.defer()
        now = datetime.now()
        prompt = t("schedule_parse_prompt", task=task, now=sched_mod.now_hint(now))
        try:
            from engine.meta import ask_haiku
            raw = await ask_haiku(prompt)
        except Exception as e:  # noqa: BLE001
            await inter.followup.send(t("schedule_create_failed", e=e))
            return

        parsed = sched_mod.extract_json(raw)
        if parsed is None:
            await inter.followup.send(t("schedule_parse_failed", result=(raw or "")[:300]))
            return

        text = str(parsed.get("task") or "").strip() or task
        cron = str(parsed.get("cron") or "").strip()
        if cron and not sched_mod.HAS_CRONITER:
            await inter.followup.send(t("schedule_no_croniter", cron=cron))
            return
        if cron and not sched_mod.valid_cron(cron):
            await inter.followup.send(t("schedule_bad_cron", cron=cron))
            return

        raw_next = str(parsed.get("next_run") or "")
        nxt = sched_mod.parse_local(raw_next)
        if nxt is None or nxt <= now:
            # 模型沒給時間／給了認不得的字串／算到過去 → 有 cron 就自己往後推一次
            fixed = sched_mod.next_after(cron, now)
            if fixed is None:
                key = "schedule_no_time" if nxt is None else "schedule_past"
                await inter.followup.send(t(key, when=raw_next[:120] or t("unknown")))
                return
            nxt = fixed

        entry = sched.add(user_id=inter.user.id, channel_id=inter.channel_id,
                          task=text, cron=cron, next_run=nxt)
        embed = discord.Embed(title=t("schedule_created_title"), color=discord.Color.blue())
        embed.add_field(name=t("field_task"), value=entry["task"][:1024], inline=False)
        embed.add_field(name=t("field_cron"),
                        value=f"`{entry['cron']}`" if entry["cron"] else t("once"), inline=True)
        embed.add_field(name=t("field_next_run"), value=entry["next_run"][:16], inline=True)
        embed.set_footer(text=f"ID: {entry['id']}")
        await inter.followup.send(embed=embed)

    @tree.command(name="schedules", description=t("cmd_schedules_desc"))
    async def cmd_schedules(inter: discord.Interaction) -> None:
        if not await auth.check(inter):
            return
        items = sched.list()
        if not items:
            await inter.response.send_message(t("schedules_none"), ephemeral=True)
            return
        # 選單上限 25 個，訊息還有 2000 字上限——兩個都到不了才繼續放，
        # 而且下拉選單只放「有列出來的」那幾筆，看到什麼就能刪什麼
        lines = [t("schedules_title")]
        used = 0
        shown: list[dict[str, Any]] = []
        for it in items[:MAX_OPTIONS]:
            ln = _line(it)
            if used + len(ln) + 1 > MAX_TEXT:
                break
            used += len(ln) + 1
            lines.append(ln)
            shown.append(it)
        if len(shown) < len(items):
            lines.append(t("schedules_more", n=len(items) - len(shown)))
        await inter.response.send_message("\n".join(lines),
                                          view=_DeleteView(sched, auth, shown))
