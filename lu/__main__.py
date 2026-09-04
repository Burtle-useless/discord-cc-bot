"""陸的進入點：`python -m lu`（上線）或 `python -m lu --check`（乾跑，不連 Discord）。

啟動順序不能換：
  1. 讀 `.env`（settings，完全不碰 engine）
  2. `bootstrap.prepare()` 設好 BUTLER_DATA_DIR 等環境變數並把
     server 目錄放上 sys.path
  3. 這之後才 import 任何 engine 模組
理由見 `bootstrap` 的模組說明——晚一步，陸就會寫進 `server/data/` 而不是自己的 `data/`。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys
from pathlib import Path
from typing import Any

from . import settings as settings_mod
from .settings import LOCK_PORT, Settings

log = logging.getLogger("lu")


def _setup_log(data_dir: Path) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("discord").setLevel(logging.WARNING)


def _single_instance() -> socket.socket | None:
    """單一實例鎖：綁得住 port 就是第一隻。

    用 socket 而不是鎖檔——行程被強制結束時 port 自動釋放，鎖檔會留下來擋住下一次
    啟動。port 號跟舊 discord_bot.py 相同，watchdog 與重啟腳本拿它當「活著」的判準。
    """
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def build_bot(st: Settings) -> tuple[Any, Any]:
    """建 discord client 與 BotContext，把事件處理器掛上去。不連線。"""
    import discord
    from discord import app_commands

    from . import intake, ledger, voice
    from .bootstrap import build
    from .commands import register_all
    from .profile import conv_id as conv_of
    from .sidebar import Sidebar

    ctx = build(st)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    ctx.client = client
    ctx.sidebar = Sidebar(
        client, ctx.auth, ctx.worker, ctx.frontends,
        category_name=st.sidebar_category, entry_name=st.sidebar_entry,
        default_cwd=st.default_cwd,
    )

    @client.event
    async def on_ready() -> None:  # noqa: D401
        log.info("陸上線：%s", client.user)
        for guild in client.guilds:
            await ctx.sidebar.ensure(guild)
        ctx.auth.entry_channel_id = ctx.sidebar.entry_id
        intake.sweep_tmp(st.data_dir / "tmp")
        # 排程迴圈要有 event loop 才起得來，所以掛在這裡而不是 register()
        sched = getattr(ctx, "scheduler", None)
        if sched is not None:
            sched.start()
        # Whisper 閒置卸載：使用者的顯示卡要拿來玩遊戲，不能一直被佔著
        ctx.keep_task(asyncio.create_task(voice.reaper()))
        n = await ctx.worker.restore_pending()
        if n:
            log.info("限流等待中的 %d 則訊息已重新排隊", n)
        await _push_update(ctx)
        try:
            await tree.sync()
        except Exception:  # noqa: BLE001
            log.exception("同步指令樹失敗")

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not ctx.auth.user_ok(message.author.id):
            return
        ch = message.channel
        entry = ctx.sidebar.is_entry(ch.id)
        if not entry and not ctx.auth.channel_ok(ch.id):
            return
        # 入口頻道打字＝開新對話：那個頻道就地轉正，這句話就是新對話的第一句
        if entry:
            if not await ctx.sidebar.promote_entry(ch):
                await ch.send(_t("entry_promote_failed"))
                return
            ctx.auth.entry_channel_id = ctx.sidebar.entry_id
        text, _voice = await intake.collect(message, st.data_dir / "tmp")
        if not text:
            return
        # 原話落帳本：對話會被壓縮，這份不會。/recall 靠它核對記憶（見 lu.ledger）
        ledger.append(st.data_dir, ch.id, message.author.display_name, text)
        conv = conv_of(ch.id)
        fe = ctx.frontend_for(conv)
        fe.last_author = message.author
        res = await ctx.hooks().submit(conv, text, message.author.display_name)
        await fe.mark_submitted(message, res.msg_id, queued=res.queued, steered=res.steered)
        ctx.keep_task(asyncio.create_task(ctx.sidebar.bump_to_top(ch)))

    @client.event
    async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
        await ctx.sidebar.on_channel_delete(channel)

    @client.event
    async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
        await ctx.sidebar.on_channel_create(channel)

    n = register_all(tree, ctx)
    log.info("掛上 %d 個指令", n)
    ctx.tree = tree
    return client, ctx


def _t(key: str) -> str:
    from .i18n import t
    return t(key)


async def _push_update(ctx) -> None:
    """版本變了就把更新公告推到 UPDATE_CHANNEL。推成功才記下版號。"""
    from . import version
    from .frontend import send_long
    st = ctx.settings
    if not st.update_channel:
        return
    changed, body = version.check_and_push(st.data_dir)
    if not changed:
        return
    ch = ctx.client.get_channel(st.update_channel)
    if ch is None:
        log.warning("更新公告頻道 %s 找不到", st.update_channel)
        return
    head = _t(f"update_head_{version.CHANGE_TYPE}").format(ver=version.VERSION)
    try:
        await send_long(ch, f"{head}\n{body}", tmp_dir=st.data_dir / "tmp")
    except Exception:  # noqa: BLE001
        log.exception("推更新公告失敗")
        return
    version.mark_pushed(st.data_dir)
    log.info("已推送 %s 的更新公告", version.VERSION)


async def _run(st: Settings) -> int:
    client, ctx = build_bot(st)
    if not st.token:
        log.error("沒有 DISCORD_TOKEN，無法上線")
        return 2
    try:
        await client.start(st.token)
    finally:
        await ctx.close()
        await client.close()
    return 0


def check(st: Settings) -> int:
    """乾跑：不連 Discord，只確認接線都成立。回 exit code。"""
    from .bootstrap import prepare
    prepare(st)
    client, ctx = build_bot(st)
    from engine import profiles
    p = profiles.resolve("dc:123")
    from engine.options import build_options
    from engine.state import get_state
    opts = build_options(get_state("dc:123"), ctx.frontend_for("dc:123"))
    ok = p.name == "lu" and "\n" not in p.append and opts.model is not None
    print(f"profile={p.name} append={len(p.append)} 字 換行={p.append.count(chr(10))}")
    print(f"model={opts.model} cwd={opts.cwd} 工具={list(opts.mcp_servers)}")
    print(f"指令={len(ctx.tree.get_commands())} 個　資料目錄={st.data_dir}")
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lu", description="陸：Discord 上的 Claude Code")
    ap.add_argument("--check", action="store_true", help="乾跑：只驗接線，不連 Discord")
    args = ap.parse_args(argv)

    st = settings_mod.load()
    _setup_log(st.data_dir)
    from .bootstrap import prepare
    prepare(st)
    if args.check:
        return check(st)
    lock = _single_instance()
    if lock is None:
        log.error("port %d 已被佔用，應該已經有一隻在跑了", LOCK_PORT)
        return 0
    try:
        return asyncio.run(_run(st))
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
