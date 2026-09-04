"""長駐 ClaudeSDKClient 池。

每個對話維持一個活進程，活躍期間不關——同進程同 session、不 fork、不留碎片；
閒置逾時才回收釋放記憶體，下次以 resume 接回。

池子裡放的是 `Mailbox` 而不是裸 client：訊息的讀取權屬於對話、不屬於回合
（理由見 `mailbox` 的模組說明）。取得 client 的地方一律連同它的收發中樞一起拿，
才不會有人繞過去直接讀串流——那會跟常駐讀取者搶訊息。
"""
from __future__ import annotations

import asyncio
import logging
import time

from claude_agent_sdk import ClaudeSDKClient

import config
from protocol import Frontend

from . import bg_notify, models
from .history import session_exists
from .mailbox import Mailbox
from .options import build_options
from .state import ConvState, eff_effort, eff_model

log = logging.getLogger(__name__)

_clients: dict[str, Mailbox] = {}
_used: dict[str, float] = {}        # 最後使用時間（閒置回收與 LRU 判斷）
_sigs: dict[str, tuple] = {}        # 設定指紋（變了就重建）
_locks: dict[str, asyncio.Lock] = {}   # 每個對話一把，見 [_lock_for]


def _lock_for(cid: str) -> asyncio.Lock:
    """取得某對話的建立鎖。

    `acquire` 中間有 `await c.connect()`，那是個把控制權交還事件迴圈的中斷點：
    同一對話的兩則訊息若同時進來（送出＋排隊的下一則、或 SSE 重連觸發的補跑），
    兩邊都會看到 `_clients` 還沒有東西而各建一個 client，後寫入的把先寫入的
    從字典裡擠掉——**被擠掉的那個進程沒有人會再去 disconnect 它**，
    node 進程就這樣留在背景吃記憶體直到服務重啟。

    一個對話一把鎖而不是一把全域鎖：全域鎖會讓「某個對話的 CLI 卡在 connect」
    連帶凍住所有其他對話。代價是 `MAX_CLIENTS` 在多個對話同時首連時可能短暫
    多出一兩個——那只是多佔一點記憶體，reaper 會收，比整組卡死好談。

    這個函式本身沒有 await，所以在單一事件迴圈裡是原子的，不需要再包一層鎖。
    """
    lk = _locks.get(cid)
    if lk is None:
        lk = _locks[cid] = asyncio.Lock()
    return lk


def client_sig(state: ConvState) -> tuple:
    """長駐 client 的設定指紋。

    不含 session_id——sid 會在正常對話中由 client 自己產出，納入會造成無謂重建。
    cwd／生效 model／生效 effort 任一改變即代表要用新設定重建。
    用「生效值」（含帳號預設層）：改帳號預設時，未單獨覆寫的對話下次會自動重建。

    _no_think 也納入：關思考重試時指紋自然不符 → client 以新 thinking 設定重建，
    且重建會帶 resume 接回同一個 session，對話歷史不中斷。旗標還原後同理換回來。
    """
    return (str(state.cwd), eff_model(state), eff_effort(state), state._no_think)


async def drop(conv_id: str) -> None:
    """關閉並移除某對話的長駐 client。永不拋例外。

    **關掉進程等於殺掉跑在裡面的背景工作**，所以要順手把帳結掉：那些工作不會
    再送任何訊息過來，帳上留著就是永遠停在「進行中」的卡片（見
    `bg_notify.orphan_all`）。畫面不會立刻更新——`drop` 拿不到 frontend，
    這裡刻意不為了推一則事件把 transport 反向拉進來——但下一次 snapshot
    就會是對的，而在那之前使用者看到的也已經不是假的「還在跑」。
    """
    box = _clients.pop(conv_id, None)
    _used.pop(conv_id, None)
    _sigs.pop(conv_id, None)
    if box is not None:
        await box.close()
    gone = bg_notify.orphan_all(conv_id)
    if gone:
        log.info("回收 %s 的連線，順帶結掉 %d 件背景工作", conv_id, gone)


def peek(conv_id: str) -> Mailbox | None:
    """取得該對話**現有**的收發中樞，沒有就回 None。絕不建立新的。

    給「只在連線還活著時才有意義」的操作用——目前是停止背景工作。那件工作
    跑在某個 CLI 進程裡，進程如果已經被回收，工作也早就跟著沒了，這時開一個
    新進程只是白白付啟動時間再對它下一道沒有對象的指令。
    """
    box = _clients.get(conv_id)
    return box if box is not None and box.alive() else None


async def acquire(state: ConvState, frontend: Frontend) -> Mailbox:
    """取得該對話的收發中樞；無、或設定指紋已變，則（丟棄後）新建並連線。

    只有首次連線才帶 resume 接回舊 session；之後同一 client 多輪都在同進程同 session。
    """
    cid = state.conv_id
    # 整段進鎖，connect 期間才不會有第二個呼叫也開一個進程（見 [_lock_for]）。
    # 已有可用 client 的快路徑也要進來，但那條路上沒有 await，鎖是立刻拿到的。
    async with _lock_for(cid):
        box = _clients.get(cid)
        # 讀取者死了就等於這條連線廢了（串流關閉或 pump 出錯），拿去用只會立刻
        # 再炸一次。當成沒有，往下重建。
        if box is not None and not box.alive():
            log.warning("對話 %s 的讀取者已停止，重建連線", cid)
            await drop(cid)
            box = None
        if box is not None and _sigs.get(cid) == client_sig(state):
            _used[cid] = time.time()
            # 回合外事件要靠它送出去，每次都換成最新的那個（見 Mailbox.frontend）
            box.frontend = frontend
            return box
        if box is not None and bg_notify.active(cid):
            # 設定變了，但這個進程裡還有背景工作在跑：重建＝殺進程＝那些工作陪葬。
            # 先沿用舊設定把這回合跑完，背景工作結束後下一次 acquire 自然重建。
            log.info("對話 %s 設定已變，但有 %d 件背景工作在跑，先沿用舊連線",
                     cid, len(bg_notify.active(cid)))
            _used[cid] = time.time()
            box.frontend = frontend
            return box
        if box is not None:                    # 設定已變 → 丟棄舊的，用新設定重建
            await drop(cid)
        # 進程池上限：滿了先淘汰最久未用的（LRU）。session 不受影響，
        # 被淘汰的對話下次有訊息時自動 resume 接回，只是多付一次進程啟動時間。
        while len(_clients) >= config.MAX_CLIENTS and _used:
            await drop(min(_used, key=lambda k: _used[k]))
        options = build_options(state, frontend)
        if state.session_id:
            if session_exists(state.session_id):
                options.resume = state.session_id  # 僅首次連線需要接回
            else:
                # 逐字稿被刪或清過 → resume 會讓 CLI 直接 exit 1，而且錯誤訊息
                # 看不出是 session 的問題（見 history.session_exists），這條對話
                # 會就此永遠回不了話。寧可開新 session 斷脈絡，也不要卡死。
                log.warning("session %s 已不存在，改開新的（對話 %s）",
                            state.session_id, cid)
                state.session_id = None
        c = ClaudeSDKClient(options)
        await c.connect()
        # 連上就順手向 CLI 要一次官方模型清單（一小時內拿過會直接跳過）。
        # 這是「模型選項跟著官方更新」唯一的資料來源，不是額外的功能。
        await models.refresh_from(c)
        # on_wake：模型在回合外自己開口（背景工作跑完後 CLI 原生的那個週期）
        # 要開成 wake 回合接住，不是丟到 idle。派工函式由 transport 註冊。
        box = Mailbox(cid, c, frontend, bg_notify.handle, on_wake=bg_notify.on_wake)
        # 連上就開始讀，不等第一個回合。這是整個設計的重點：讀取者的壽命跟著
        # 連線走，不跟著回合走，回合外的訊息才有人接。
        box.start()
        _clients[cid] = box
        _sigs[cid] = client_sig(state)
        _used[cid] = time.time()
        return box


async def reaper() -> None:
    """背景工作：定期回收閒置過久的 client，釋放記憶體與 VRAM。"""
    while True:
        await asyncio.sleep(config.CLIENT_IDLE_TIMEOUT)
        now = time.time()
        for cid in [k for k, t0 in _used.items() if now - t0 > config.CLIENT_IDLE_TIMEOUT]:
            await drop(cid)


async def shutdown() -> None:
    """服務收工：關掉所有 client，避免留下孤兒 node 進程。"""
    for cid in list(_clients):
        await drop(cid)


def stats() -> dict[str, int]:
    return {"clients": len(_clients), "max": config.MAX_CLIENTS}
