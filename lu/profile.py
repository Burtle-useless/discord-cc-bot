"""陸的 profile：system prompt append 與工具組，掛在 `dc:` 前綴上。

**只能在 `bootstrap.prepare()` 之後 import**（要 `server/engine` 在 sys.path 上）。

append 從 i18n 的 `system_prompt` 來，**一個換行都不能有**——含換行會讓 CLI 的
initialize 握手卡 60 秒（見 `server/engine/options.py`）。這裡刻意不做 sanitize：
`build_options` 會再過一次，但靠它等於把炸彈留給下一個改文案的人；
tests/test_boot.py 直接對原文釘死。
"""
from __future__ import annotations

from engine import file_tools
from engine.profiles import Profile, register
from engine.state import ConvState

from .i18n import t

# 對話 id 前綴：Discord 頻道 → `dc:<channel_id>`。同時決定 profile。
CONV_PREFIX = "dc:"


def _lu_servers(state: ConvState) -> dict:
    """陸的工具：只有傳檔。做出來的圖表與報告要能直接上傳到頻道。

    傳檔工具是每個對話一份（file_tools.server_for），送出去的檔案才知道自己是從
    哪個頻道發起的（file.offer 事件據此落到正確的頻道）。行事曆／看板／位置那組
    是手機 App 端的生活資料，陸不掛。
    """
    return {file_tools.SERVER_NAME: file_tools.server_for(state.conv_id)}


def build() -> Profile:
    """依目前介面語言組出陸的 profile。BOT_LANG 換了要重建（啟動時做一次）。"""
    return Profile(name="lu", append=t("system_prompt"), servers=_lu_servers, src_default="Discord")


def install() -> Profile:
    """把陸掛到 `dc:` 前綴。重複呼叫只是覆寫，無害。"""
    p = build()
    register(CONV_PREFIX, p)
    return p


def conv_id(channel_id: int) -> str:
    return f"{CONV_PREFIX}{channel_id}"


def channel_id(conv: str) -> int | None:
    """`dc:<id>` → id；不是陸的對話回 None。"""
    if not conv.startswith(CONV_PREFIX):
        return None
    tail = conv[len(CONV_PREFIX):]
    return int(tail) if tail.isdigit() else None
