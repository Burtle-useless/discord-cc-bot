"""事件模型：引擎對外只發「語意事件」，不發渲染好的字串。

cc-bot 為了塞進 Discord 2000 字上限而生的整套渲染機制（動畫、翻頁、狀態列組字串）
在這裡全部不存在——手機端 LazyColumn 天生就是往下累積，怎麼畫由前端決定。

`seq` / `conv_id` / `turn_id` 三個欄位從第一版就必須全帶上：
少 seq 就不能斷線續傳、少 conv_id 就不能多對話共用一條連線、
少 turn_id 就沒辦法把空回覆重試畫成「重試 #2」而不是看起來像 AI 精神錯亂。
補上去要同時動 Server、RingBuffer、Room schema 與 UI 四處，代價極高。
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal, get_args

EventType = Literal[
    "turn.start",       # 回合開始：{"prompt": 使用者原文, "origin": "user"|"wake",
                        #   "wake": dict}
                        #   origin="wake"＝助理自己醒來的那一輪（背景工作跑完後
                        #   CLI 原生開的週期，見 engine.turn.handle_wake），
                        #   prompt 是空的；wake 帶最近剛結束的那件背景工作
                        #   （形狀同 bg.state 的每一筆），對不上就是空 dict。
                        #   畫面上該畫一行「背景工作『x』完成，助理接手」，
                        #   不是使用者氣泡。
    "turn.end",         # 回合結束：{"ok": bool, "elapsed": float}
    "thinking.delta",   # 思考逐字：{"d": str}
    "text.delta",       # 回覆逐字：{"d": str}
    "step.commit",      # 一則 AssistantMessage 定稿：{"text": str, "think_digest": str}
    "tool.call",        # 工具呼叫：{"tool","summary","raw","dangerous","icon"}
    "reply.final",      # 本回合最終回覆：{"markdown": str, "files": list,
                        #   "pending_ask": bool}
                        #   **每一輪都會發**：自動續跑每續一輪一則、壓縮核對再一則。
                        #   所以它代表「這一輪定稿了」，不代表整則訊息做完了。
                        #   要判斷做完沒有，看 turn.done。
    "turn.done",        # 整則使用者訊息真的收工：{"used_tool","markdown",
                        #   "pending_ask","elapsed_ms"}
                        #   **手機端的「做完了」推播只認這一則。**綁在 reply.final
                        #   上時會在續跑的第一輪就推播，使用者點進來助理還在跑。
                        #   出錯不發（error 事件自己會推「出狀況了」）。

    "ask.request",      # 需要使用者決定：{"ask_id", "kind", "title", "body", "raw", "choices"}
    "ask.resolved",     # 已有答案（供其他裝置同步）：{"ask_id", "choice_id"}
    "status",           # 心跳狀態：{"elapsed","model","effort","ctx_tokens","bg","phase"}
                        #   phase="compacting" 代表這段時間在整理記憶，不是在回話
    "error",            # 錯誤：{"kind","detail","retryable","attempt","max"}
    "notify",           # 長任務完成，觸發推播：{"title","body"}
    "bg.state",         # 背景工作的當下全貌：{"tasks": list[dict]}
                        #   每筆帶 id／desc／status／started_at／finished_at／
                        #   last_tool／tokens／tool_uses／summary（見 BgTask.to_wire）。
                        #   同一份清單在回合進行中由 status.bg 帶著走，但那個
                        #   隨回合結束而停，而背景工作不會停。這一則補的就是
                        #   回合外的變化，畫面上那幾張卡片靠它才留得住。
                        #   已完成的也在裡面，直到使用者下次發言才收起來。
    "seq.gap",          # 續傳斷層，叫前端改拉 snapshot：{"from","to"}
    "user.message",     # 使用者訊息回音（多裝置同步的基礎）：
                        #   {"text","msg_id","queued","steered","attachments"}
                        #   attachments 是 [{name,path,bytes,mime}]，**結構化欄位**：
                        #   前端據此畫縮圖與檔案卡，路徑只在送給模型的那一份出現
                        #   （turn.stamp 接上去，跟時間戳同一個道理）。先前是 App
                        #   自己把路徑拼進本文，於是那串路徑成了使用者氣泡裡的文字。
                        #   **只有人真的送出訊息時才發。** 助理自己醒來的那一輪
                        #   （背景工作跑完後的 wake 回合）沒有任何使用者訊息，
                        #   由 turn.start 的 origin="wake" 說明，不偽造一則發言。
    "message.taken",    # 排著的訊息被讀進這一輪了：{"msg_ids": list[str]}
    "message.dropped",  # 排著的訊息隨停止一起取消：{"msg_ids": list[str]}
    "agenda.changed",   # 行事曆／鬧鐘／記帳／課表有變動，叫 App 重拉並重排鬧鐘：{"what": str}
    "file.offer",       # 助理要傳檔案給手機：{"file_id","name","bytes","note"}
    "device.request",   # 跟手機要一份即時資料：{"req_id","kind","timeout_sec"}
                        #   kind="location" 時 App 抓一次位置後 POST 回來。
                        #   **靜默事件**：App 自己處理完自己回，不畫任何 UI，
                        #   使用者不會知道發生過（跟 ask.request 的差別就在這）。
    "stream.reset",     # 伺服器重啟了，本地游標屬於上一個世代：前端清掉本地軌跡
                        #   改用 snapshot 重建（hub.stream 在偵測到舊世代游標時發）。
    "kanban.changed",   # 看板有變動（助理的工具或 App 的操作），叫另一端重拉。
]

# 執行期用的集合。`Literal` 只在型別檢查時有意義，先前 `stream.reset` 與
# `kanban.changed` 兩個事件伺服器發了幾個禮拜、兩端前端也都在接，這份清單卻
# 沒有它們——沒有任何東西會炸。`make_event` 現在對照這個集合，漏登記的當場出聲。
EVENT_TYPES: Final[frozenset[str]] = frozenset(get_args(EventType))

# 這幾類事件量大且可合併，弱網下合併後再送，避免逐字事件把手機淹掉
COALESCABLE: Final[frozenset[str]] = frozenset({"thinking.delta", "text.delta"})


@dataclass(frozen=True, slots=True)
class Event:
    """一則事件。data 內容自由演進，外層結構固定。"""

    seq: int            # 全域單調遞增，對應 SSE 的 id: 欄位，續傳錨點
    conv_id: str        # 對話 id，一條連線承載多個對話靠它分流
    turn_id: str        # 回合 id，UI 靠它把重試分組
    type: EventType
    ts: float
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> dict[str, str]:
        """轉成 sse-starlette 需要的欄位形狀。"""
        import json
        # ts 一起送：前端要畫訊息時間與日期分隔，不該各自拿本地時鐘猜
        payload = {"conv_id": self.conv_id, "turn_id": self.turn_id, "ts": self.ts, **self.data}
        return {
            "id": str(self.seq),
            "event": self.type,
            "data": json.dumps(payload, ensure_ascii=False),
        }


class SeqGen:
    """全域事件序號產生器。

    刻意不從 0 起算而是用啟動時間戳當高位——服務重啟後序號不會倒退，
    手機拿著舊的 Last-Event-ID 重連時才不會誤判成「這些我都收過了」而丟掉新事件。
    """

    def __init__(self) -> None:
        self._it = itertools.count(int(time.time() * 1000))

    def next(self) -> int:
        return next(self._it)


_seq = SeqGen()


def make_event(
    conv_id: str,
    turn_id: str,
    type_: EventType,
    **data: Any,
) -> Event:
    """建立一則事件並自動編號。事件名必須在 `EventType` 清單裡，否則直接炸——
    那份清單是兩個前端對照的契約，漏登記的事件等於沒有文件。"""
    if type_ not in EVENT_TYPES:
        raise ValueError(f"未登記的事件型別：{type_!r}（加進 protocol/events.py 的 EventType）")
    return Event(
        seq=_seq.next(),
        conv_id=conv_id,
        turn_id=turn_id,
        type=type_,
        ts=time.time(),
        data=data,
    )
