"""DiscordFrontend：假頻道、假訊息，不連 Discord。

驗三件事：畫布編輯有節流（不會每則事件打一次 API）、STOPPED 會把半截送出來、
ask 的按鈕流程回得了 AskResponse。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# 路徑要在 import lu 之前排好：frontend 會 import 引擎的 outbox／protocol。
# 資料目錄也一樣——engine 的 config 在 import 當下就把它凍成常數，
# 晚一步設就會寫到正式的 data\。兩件事都在 _env.setup() 裡。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env  # noqa: E402

_env.setup("lu-frontend-")

from lu.canvas import RawStore  # noqa: E402
from lu.frontend import DiscordFrontend, Hooks  # noqa: E402
from lu.render import CanvasState  # noqa: E402

FAILED: list[str] = []
CONV = "dc:999"


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


class FakeMessage:
    def __init__(self, channel: "FakeChannel", content: str) -> None:
        self.channel = channel
        self.content = content
        self.edits = 0
        self.deleted = False
        self.reactions: list[str] = []

    async def edit(self, **kw) -> None:
        self.content = kw.get("content", self.content)
        self.edits += 1

    async def delete(self) -> None:
        self.deleted = True

    async def add_reaction(self, emoji) -> None:
        self.reactions.append(str(emoji))

    async def remove_reaction(self, emoji, _who=None) -> None:
        if str(emoji) in self.reactions:
            self.reactions.remove(str(emoji))

    async def clear_reaction(self, emoji) -> None:
        await self.remove_reaction(emoji)


class FakeChannel:
    id = 999

    def __init__(self) -> None:
        self.sent: list[FakeMessage] = []
        self.guild = None

    async def send(self, content: str | None = None, **kw) -> FakeMessage:
        m = FakeMessage(self, content or "")
        self.sent.append(m)
        return m

    async def typing(self):  # noqa: D401
        class _N:
            async def __aenter__(self_inner): return None
            async def __aexit__(self_inner, *a): return False
        return _N()


def _ev(type_: str, **data):
    from protocol import make_event
    return make_event(CONV, "t-1", type_, **data)


def _frontend(ch: FakeChannel, tmp: Path, interval: float = 60.0) -> DiscordFrontend:
    async def _submit(*_a, **_k):
        raise AssertionError("這個測試不該送訊息")

    async def _renamed(*_a, **_k):
        return None

    hooks = Hooks(
        channel_of=lambda _cid: ch,
        submit=_submit,
        on_renamed=_renamed,
        user_ok=lambda _uid: True,
        raw_store=RawStore(None),
        ctx_limit=lambda _c: 0,
        tmp_dir=tmp,
    )
    return DiscordFrontend(CONV, ch.id, hooks, edit_interval=interval)


async def _drain(fe: DiscordFrontend, want: int, timeout: float = 3.0) -> bool:
    """等前端把 want 則事件處理完。"""
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if fe.handled >= want:
            return True
        await asyncio.sleep(0.01)
    return False


async def test_throttle(tmp: Path) -> None:
    print("\n[畫布節流]")
    ch = FakeChannel()
    fe = _frontend(ch, tmp, interval=60.0)     # 60 秒才准編輯一次
    evs = [_ev("turn.start", prompt="數到十", origin="user")]
    evs += [_ev("text.delta", d=str(i)) for i in range(20)]
    for e in evs:
        await fe.emit(e)
    ok = await _drain(fe, len(evs))
    check("事件都處理完了", ok, f"handled={fe.handled}")
    check("只送一則畫布", len(ch.sent) == 1, str(len(ch.sent)))
    edits = ch.sent[0].edits if ch.sent else -1
    check("節流：20 則 delta 沒有變成 20 次編輯", edits <= 1, f"edits={edits}")
    await fe.close()


async def test_stopped(tmp: Path) -> None:
    print("\n[停止保留半截]")
    ch = FakeChannel()
    fe = _frontend(ch, tmp, interval=0.0)
    for e in (_ev("turn.start", prompt="數到一百", origin="user"),
              _ev("text.delta", d="一、二、三"),
              _ev("error", kind="STOPPED", detail="好，我停下了。")):
        await fe.emit(e)
    await _drain(fe, 3)
    body = "\n".join(m.content for m in ch.sent)
    check("半截的字有送出來", "一、二、三" in body, body[:120])
    check("有講這是中斷前的部分", "停" in body, body[-80:])
    await fe.close()


async def test_ask(tmp: Path) -> None:
    print("\n[提問按鈕]")
    from protocol import AskChoice, AskRequest
    ch = FakeChannel()
    fe = _frontend(ch, tmp, interval=0.0)
    req = AskRequest(
        kind="choose", title="要哪一個？", body="說明",
        choices=(AskChoice(id="a", label="甲"), AskChoice(id="b", label="乙")),
        timeout_sec=5.0,
    )
    task = asyncio.create_task(fe.ask(req))
    await asyncio.sleep(0.15)
    pend = fe.pending_asks()
    check("提問登記成 pending", len(pend) == 1, str(pend)[:120])
    check("問題有送進頻道", any("要哪一個" in m.content for m in ch.sent),
          str([m.content[:40] for m in ch.sent]))
    ask_id = pend[0]["ask_id"]
    check("答別的 id 不會誤中", not fe.resolve("不存在的", "a"))
    check("答得進去", fe.resolve(ask_id, "b"))
    resp = await asyncio.wait_for(task, timeout=3)
    check("拿回選的那個", resp is not None and resp.choice_id == "b", str(resp))
    check("答完就不在 pending 裡", fe.pending_asks() == [])
    await fe.close()


async def test_ask_timeout(tmp: Path) -> None:
    print("\n[提問逾時]")
    from protocol import AskChoice, AskRequest
    ch = FakeChannel()
    fe = _frontend(ch, tmp, interval=0.0)
    req = AskRequest(kind="choose", title="等一下下", choices=(AskChoice(id="a", label="甲"),),
                     timeout_sec=0.3)
    resp = await fe.ask(req)
    check("逾時回 None（fail-closed）", resp is None, str(resp))
    check("逾時後清乾淨", fe.pending_asks() == [])
    await fe.close()


async def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        await test_throttle(tmp)
        await test_stopped(tmp)
        await test_ask(tmp)
        await test_ask_timeout(tmp)
    print(f"\n{'全部通過' if not FAILED else '失敗：' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
