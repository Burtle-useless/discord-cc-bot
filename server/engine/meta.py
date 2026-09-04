"""一次性的輕量 meta 查詢（標題生成等），不留 session 檔。

對照 cc-bot 的 _ask_haiku / _purge_title_shell / _generate_title。
"""
from __future__ import annotations

import re
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

import config

from . import models
from .mailbox import iter_messages

TITLE_PROMPT = (
    "用不超過 12 個字幫這段對話取一個貼切的中文短標題，"
    "只輸出標題本身，不要引號不要句號。對話開頭："
)

# 錯誤樣態不准當標題——cc-bot 曾把「Failed to authenticate. API Error: 401」
# 存成標題還改了頻道名，之後每回合狀態列都掛著它，看起來像持續壞掉。
_ERRORISH = re.compile(r"(?i)failed to authenticate|api error|oauth|\b401\b|\b429\b|\b529\b")


def _session_has_body(jf: Path) -> bool:
    """session 檔是否含對話本體（而不是只有 auto-title 的空殼）。"""
    try:
        # 同 search.py：沒有 errors="replace" 的話，壞掉的逐字稿會讓解碼拋
        # UnicodeDecodeError 穿過只接 OSError 的 except，空殼判斷連帶失效。
        with jf.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type":"user"' in line or '"type":"assistant"' in line:
                    return True
    except OSError:
        pass
    return False


def _purge_title_shell(meta_sid: str | None) -> None:
    """清掉 meta 查詢殘留的空殼 session 檔。

    已設 no-session-persistence 抑制對話本體，但 CLI 內建 auto-title 偶爾會搶在
    行程結束前寫入一行，留下只有標題、無本體的空殼。只刪「這次查詢自己的 sid」
    且確認無本體才刪——真實對話一定有本體，絕不會被誤刪。
    """
    if not meta_sid:
        return
    for jf in config.claude_projects_dir().glob(f"*/{meta_sid}.jsonl"):
        try:
            if not _session_has_body(jf):
                jf.unlink()
        except OSError:
            pass
        break


async def ask_haiku(prompt: str) -> str:
    """一次性、不留 session 檔的輕量呼叫（標題生成用）。"""
    return await ask_once(prompt, models.META_MODEL)


async def ask_once(prompt: str, model: str | None = None) -> str:
    """一次性、不留 session 檔的呼叫。`model=None` 交給 CLI 用帳號預設。

    標題生成用 Haiku（快又便宜）；交接稿這種**品質重於成本**的則傳這條對話自己的
    模型進來。`no-session-persistence` 讓它不寫逐字稿，否則每取一次標題就在
    `~/.claude/projects` 多一個空殼 session，`/sessions` 清單會被灌爆。
    """
    opts = ClaudeAgentOptions(
        cwd=str(config.DEFAULT_CWD),
        cli_path=config.CLAUDE_CLI,
        model=model,
        permission_mode="bypassPermissions",
        extra_args={"no-session-persistence": None},
    )
    out, meta_sid = "", None
    async with ClaudeSDKClient(opts) as c:
        await c.query(prompt)
        async for msg in iter_messages(c):
            if isinstance(msg, ResultMessage):
                out = msg.result or ""
                meta_sid = msg.session_id
                break
    _purge_title_shell(meta_sid)
    return out.strip()


async def generate_title(convo_text: str) -> str | None:
    """由對話開頭內容生成短標題。失敗或長得像錯誤訊息就回 None。

    吃的是「一段對話文字」不是「第一則訊息」：使用者的開場常常是「幫我看一下」
    這種沒有資訊量的話，只讀它就只能生出同等級的標題。3000 字對齊 cc-bot 的
    _read_session_text 預設值——夠涵蓋前幾輪往返，又不會讓 Haiku 讀太久。
    """
    try:
        raw = await ask_haiku(TITLE_PROMPT + convo_text[:3000])
    except Exception:
        return None
    # 先 splitlines 再取，不要靠 `if raw` 判斷。raw 是 "   " 時那個條件為真，
    # 但 `"   ".strip()` 是空字串、`"".splitlines()` 是 `[]`，`[0]` 就 IndexError——
    # 而這行在上面的 try 外面，例外會一路穿出去讓標題端點 500，
    # 該對話從此不會再有標題（生成只在第一則訊息後跑一次）。
    lines = (raw or "").strip().splitlines()
    title = lines[0] if lines else ""
    title = title.strip("「」\"'*#＊ 　")[:24]
    if not title or _ERRORISH.search(title):
        return None
    return title
