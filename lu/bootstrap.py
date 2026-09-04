"""把陸接到引擎上：環境變數、sys.path、profile、ports、Worker、BotContext。

**呼叫順序是這個模組存在的唯一理由。** `prepare()` 必須在 import 任何 engine 模組
之前跑完——`server/config.py` 在 import 當下就把 `DATA_DIR` 算好凍成常數，晚一步
設 `BUTLER_DATA_DIR`，陸就會把對話寫進 `server/data/`（引擎自己的預設位置）而不是
repo 根目錄的 `data/`。所以這裡的 engine import 全部是**函式內 import**，模組頂端一個都沒有。

`build()` 之後才有 `BotContext`：指令、側欄、前端都從它拿東西，測試也塞得進假的。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import Settings

log = logging.getLogger("lu")

_prepared = False


def prepare(st: Settings) -> None:
    """設環境變數、把引擎的 server 目錄放上 sys.path。可重複呼叫，只有第一次有效。

    重複呼叫時不再改環境變數：`config` 很可能已經 import 過了，那時候改也沒用，
    改了反而讓「值是什麼」跟「引擎實際在用什麼」對不上。
    """
    global _prepared
    if _prepared:
        return
    st.data_dir.mkdir(parents=True, exist_ok=True)
    (st.data_dir / "tmp").mkdir(exist_ok=True)
    # 這三個是引擎的 config 會讀的。BUTLER_DATA_DIR 決定 session.json／titles.json／
    # models.json／outbox.json／pending_resume.json 全部落在哪裡。
    os.environ["BUTLER_DATA_DIR"] = str(st.data_dir)
    os.environ["BUTLER_CWD"] = str(st.default_cwd)
    os.environ["CONFIRM_DANGEROUS"] = "1" if st.confirm_dangerous else "0"
    # 這兩個是 lu.files 在 import 當下讀的（大檔的臨時下載連結，選填）。
    # `.env` 的值不會自己進 os.environ（見 settings 的 _read_env_file），要在這裡轉過去。
    os.environ["SHARE_SCRIPT"] = st.share_script
    os.environ["SHARE_HOURS"] = str(st.share_hours)
    # .env 裡寫給引擎的那幾個（模型、思考強度、方案、人格、CLI 路徑）。少了這一行，
    # 在 .env 設 DEFAULT_MODEL 會完全沒反應也不報錯——見 settings.ENGINE_ENV_KEYS。
    os.environ.update(st.engine_env)
    server = str(st.butler_server)
    if server not in sys.path:
        sys.path.insert(0, server)
    _prepared = True


def _check_engine() -> None:
    """引擎在不在、資料目錄有沒有被指對。錯了現在就炸，不要等第一則訊息。"""
    import config  # noqa: PLC0415 — 必須在 prepare() 之後
    want = os.environ.get("BUTLER_DATA_DIR", "")
    if want and Path(config.DATA_DIR) != Path(want):
        raise RuntimeError(
            f"引擎的資料目錄是 {config.DATA_DIR}，不是陸的 {want}——"
            "prepare() 一定要在 import engine 之前跑（見 bootstrap 模組說明）",
        )


@dataclass
class BotContext:
    """一隻 bot 的全部零件。指令與事件處理器都從這裡拿東西。"""

    settings: Settings
    auth: Any                                   # lu.auth.Auth
    worker: Any                                 # engine.worker.Worker
    frontends: dict[str, Any] = field(default_factory=dict)
    sidebar: Any = None                         # lu.sidebar.Sidebar（要有 client 才建得起來）
    client: Any = None                          # discord.Client
    tree: Any = None                            # app_commands.CommandTree
    scheduler: Any = None                       # lu.scheduler.Scheduler（commands/schedule 建）
    raw_store: Any = None                       # lu.canvas.RawStore
    _tasks: set[asyncio.Task] = field(default_factory=set)

    # ── 前端 ──
    def frontend_for(self, conv: str) -> Any:
        """這條對話的 Frontend，沒有就建一個。

        engine 的 Worker 拿的就是這個函式（建構參數 `frontend_for`），所以每一條
        對話第一次被送訊息時會在這裡長出前端，不必事先登記。
        """
        fe = self.frontends.get(conv)
        if fe is None:
            from .frontend import DiscordFrontend
            from .profile import channel_id
            cid = channel_id(conv) or 0
            fe = self.frontends[conv] = DiscordFrontend(conv, cid, self.hooks())
        return fe

    def hooks(self) -> Any:
        """前端要的外部能力。每次都重建一個 Hooks（欄位全是綁好的函式，很便宜）。"""
        from .frontend import Hooks
        return Hooks(
            channel_of=self._channel_of,
            submit=self._submit,
            on_renamed=self._on_renamed,
            user_ok=lambda uid: self.auth.user_ok(uid),
            raw_store=self.raw_store,
            ctx_limit=self._ctx_limit,
            tmp_dir=self.settings.data_dir / "tmp",
        )

    # ── 給前端的四個能力 ──
    def _channel_of(self, channel_id: int) -> Any | None:
        return self.client.get_channel(channel_id) if self.client else None

    async def _submit(self, conv: str, text: str, speaker: str) -> Any:
        """使用者說了一句話。插話與排隊的判斷全在 Worker 裡，這裡只轉交。

        speaker 前綴 `[名字]: ` 跟舊 cc-bot 一樣：同一個頻道可能有好幾個人在講話，
        模型要分得出誰是誰。時間戳與來源由 engine 的 stamp 蓋。
        """
        body = f"[{speaker}]: {text}" if speaker else text
        return await self.worker.submit(conv, body, "Discord")

    async def _on_renamed(self, conv: str, title: str) -> None:
        if self.sidebar is not None:
            from .profile import channel_id
            cid = channel_id(conv)
            if cid:
                await self.sidebar.rename(cid, title)

    def _ctx_limit(self, conv: str) -> int:
        try:
            from engine.state import get_state
            from engine.turn import ctx_limit
            return ctx_limit(get_state(conv))
        except Exception:  # noqa: BLE001 — 只是狀態列的百分比，拿不到就不畫
            return 0

    # ── 背景任務 ──
    def keep_task(self, task: asyncio.Task) -> asyncio.Task:
        """留住 task 的參照直到它結束。

        asyncio 只持弱參照，`create_task` 之後不留著的話，任務可能在跑完之前
        被 GC 收走——症狀是「有時候標題就是生不出來」，而且完全沒有錯誤訊息。
        """
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(self) -> None:
        """收工：排程、前端的 render task、背景任務都停掉。"""
        if self.scheduler is not None:
            try:
                self.scheduler.stop()
            except Exception:  # noqa: BLE001
                pass
        for fe in list(self.frontends.values()):
            await fe.close()
        for tk in list(self._tasks):
            tk.cancel()
        await self.worker.shutdown()


async def _no_locate() -> dict[str, Any] | None:
    """陸沒有手機可以問位置。回 None＝「拿不到」，工具那邊會照這條路回答。"""
    return None


async def _ignore(_what: str) -> None:
    """行事曆／看板變動：那是手機 App 端的分頁，Discord 這邊沒有畫面要刷新。"""
    return None


def build(st: Settings, *, install_ports: bool = True) -> BotContext:
    """建好引擎接線與 BotContext。呼叫前必須先 `prepare(st)`。

    `install_ports=False` 給測試用：同一個進程裡跑第二次會把上一次的掛勾蓋掉，
    而 engine 的掛勾是模組全域。
    """
    _check_engine()
    from engine import ports
    from engine.worker import Worker

    from . import profile as lu_profile
    from . import semantic, voice
    from .auth import Auth
    from .canvas import RawStore

    lu_profile.install()
    voice.setup(st.data_dir)
    semantic.set_cache_file(st.data_dir / "session_vectors_e5.json")

    ctx = BotContext(
        settings=st,
        auth=Auth(st.data_dir, st.owner_id),
        worker=None,  # 下面補：Worker 要拿 ctx.frontend_for
        raw_store=RawStore(st.data_dir / "raw_store.json"),
    )

    async def _autoname(conv: str, first_message: str) -> None:
        from engine import titles
        await titles.autoname(conv, first_message, ctx.frontend_for(conv))

    ctx.worker = Worker(ctx.frontend_for, autoname=_autoname)

    async def _offer(item: dict[str, Any]) -> None:
        """引擎的傳檔工具登記了一個檔案：發成 file.offer 事件，前端負責上傳。

        走事件而不是直接上傳，是為了跟其他事件排在同一條佇列裡——檔案卡片才會
        落在回合的正確位置，而不是插在回覆中間。
        """
        from protocol import make_event
        conv = str(item.get("conv_id") or "-")
        fe = ctx.frontends.get(conv)
        if fe is None:
            return
        await fe.emit(make_event(
            conv, "-", "file.offer",
            file_id=item["file_id"], name=item["name"], bytes=item["bytes"],
            note=item.get("note", ""), mime=item.get("mime", ""),
        ))

    if install_ports:
        ports.install(ports.Ports(
            waker=ctx.worker.dispatch_wake,
            locate=_no_locate,
            offer=_offer,
            agenda_changed=_ignore,
            kanban_changed=_ignore,
        ))
    from .frontend import install_hooks
    install_hooks(ctx.hooks())
    return ctx
