"""對話 profile：依 conv_id 決定這條對話的人格（system prompt append）與工具（MCP servers）。

以前這個判斷寫死在 `options.append_for`／`servers_for` 裡：「是助理那條就全套工具，
其餘都是工作精簡版」。那個 if 只夠用到「只有一個前端」為止——同一份 engine 被第二個
前端（另一支 bot、一支 CLI、一個網頁殼）接上來之後，「其餘」就不再只有一種，那個前端
的對話要自己的人格與工具組。這裡把判斷做成前綴註冊表：conv_id 以哪個前綴開頭就用哪個
profile，最長前綴優先，`""` 是預設。

**助理那條不走前綴。** `config.PRIMARY_CONV` 是精確比對——改成前綴的話一條叫 `main2`
的工作對話會被套成助理。這條規則寫死在 `resolve` 開頭，註冊表改不動它。

兩個內建 profile 在這裡（模組載入時）註冊：
  - 助理（`PRIMARY`）：`options.SYSTEM_APPEND` ＋ agenda／kanban／location／files
  - 預設（prefix `""`）：`options.WORK_APPEND` ＋ files

**人格本身不在這裡。** 那兩份 append 是 `options` 用 `persona.load()` 讀
`server/personas/*.txt` 組出來的，這裡只負責「哪條對話拿哪一份」。要換語氣就去改文字
檔，不必動這裡；要多一種人格就在 personas/ 多放一份、再 `register` 一個 profile 指
過去。這裡 import `options` 拿那兩份 append，所以 `options` 那邊對這裡的 import 放在
函式內，頂端互相 import 會繞成一圈。

自己接第三個前端時的用法（在服務啟動時做一次就好）：

    from engine import options, profiles
    profiles.register("mybot:", profiles.Profile(
        name="mybot",
        append=options.WORK_APPEND,          # 或 persona.load("mybot") 自己組
        servers=profiles.files_only,
    ))
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import config

from . import agenda_tools, file_tools, kanban_tools, location
from .options import SYSTEM_APPEND, WORK_APPEND
from .state import ConvState


@dataclass(frozen=True, slots=True)
class Profile:
    """一種對話的設定組。

    `append` 是疊在 Claude Code 預設 prompt 之上的全文，**不可含換行**
    （`build_options` 會再過一次 `sanitize_append`，但別靠它——見 options 的說明）。
    `servers` 是函式而不是 dict：傳檔工具是每個對話一份（`file_tools.server_for`），
    要拿到 conv_id 才建得出來。
    """

    name: str                                  # "primary" / "work" / 你自己的前端名…
    append: str                                # system_prompt append 全文（無換行）
    servers: Callable[[ConvState], dict]       # 這條對話要掛的 MCP servers
    src_default: str = "手機"                   # 這個前端預設的來源名（給時間戳用）


def full_servers(state: ConvState) -> dict:
    """全套工具：行事曆／鬧鐘／記帳、看板、位置，加上傳檔。

    行事曆那組只給助理那條——那是生活資料，工作對話開著只會讓模型在
    「幫我記一下這個 bug」的時候把東西寫進他的記帳本。
    """
    return {
        agenda_tools.SERVER_NAME: agenda_tools.SERVER,
        kanban_tools.SERVER_NAME: kanban_tools.SERVER,
        location.SERVER_NAME: location.SERVER,
        file_tools.SERVER_NAME: file_tools.server_for(state.conv_id),
    }


def files_only(state: ConvState) -> dict:
    """工作對話只有傳檔：做出來的圖表與報告一樣得送到他手機上。

    傳檔工具是**每個對話一份**（見 file_tools.server_for），這樣送出去的檔案
    才知道自己是從哪一段對話出來的。
    """
    return {file_tools.SERVER_NAME: file_tools.server_for(state.conv_id)}


# 助理：全套。只給 `config.PRIMARY_CONV` 那一條，精確比對（見 resolve）。
PRIMARY = Profile(name="primary", append=SYSTEM_APPEND, servers=full_servers)

# 預設：工作精簡版。工作分頁開幾條就有幾條，所以它是「剩下的全部」。
DEFAULT = Profile(name="work", append=WORK_APPEND, servers=files_only)

# 前綴 → profile。`""` 永遠在（模組載入時放進去），所以 resolve 一定找得到東西。
_by_prefix: dict[str, Profile] = {}


def register(prefix: str, profile: Profile) -> None:
    """登記一個 profile：conv_id 以 [prefix] 開頭就用它。`""` 是預設，重登記即覆寫。

    另一個前端在啟動時呼叫，把自己的對話前綴掛上來。
    """
    _by_prefix[prefix] = profile


def resolve(conv_id: str) -> Profile:
    """這條對話該用哪個 profile。助理那條固定回 `PRIMARY`；其餘取**最長**符合的前綴。

    最長優先是為了讓 `bot:` 與 `bot:group:` 能並存——短的當那個前端的預設，
    長的覆寫其中一部分，跟路由表同一個直覺。
    """
    if conv_id == config.PRIMARY_CONV:
        return PRIMARY
    best: str | None = None
    for prefix in _by_prefix:
        if conv_id.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    if best is None:
        # 只有在有人把 "" 拿掉時才會走到這裡；那是程式錯，不是資料問題
        raise LookupError(f"沒有任何 profile 對得上 conv={conv_id!r}，連預設都不在")
    return _by_prefix[best]


register("", DEFAULT)
