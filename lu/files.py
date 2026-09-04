"""把檔案送到 Discord 頻道：塞得進就直接上傳，塞不進就看有沒有臨時下載連結可用。

`file.offer` 事件那條路（模型用 send_file 工具傳檔）與日後 /screenshot 之類都走這裡。
上限向 Discord 查 `guild.filesize_limit`（伺服器加成會抬高到 50／100 MiB），
私訊或查不到就退回免費下限——寫死 25MB 曾經讓 10MB 的檔吃 413。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import discord

from .i18n import t

log = logging.getLogger(__name__)

# Discord 未加成伺服器與私訊的上限
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024

# ── 超過上限的檔案改走臨時下載連結（選填功能）───────────────────────────────
# SHARE_SCRIPT 指向一支 PowerShell 腳本，介面必須是
#     <script> <檔案路徑> -As <輸出檔名> -Hours <時數>
# 並把下載網址印到 stdout（例如用 cloudflared 之類的工具開臨時通道）。
# **沒設就整個停用**，大檔只回報名稱與本機路徑——絕不去執行一支不存在的腳本。
SHARE_SCRIPT = (os.environ.get("SHARE_SCRIPT") or "").strip()
try:
    SHARE_HOURS = int((os.environ.get("SHARE_HOURS") or "24").strip() or 24)
except ValueError:
    SHARE_HOURS = 24
_SHARE_URL_RE = re.compile(rb"https://\S+")


def upload_limit(channel: Any) -> int:
    """該頻道實際能上傳的位元組上限。"""
    return getattr(getattr(channel, "guild", None), "filesize_limit", 0) or DEFAULT_UPLOAD_LIMIT


def ascii_name(fp: Path) -> str:
    """把檔名壓成純 ASCII：中文檔名會讓網址變成一長串 %E4%B8%AD，在通訊軟體裡容易被截斷。"""
    stem = re.sub(r"[^A-Za-z0-9._-]", "", fp.stem).strip("._-")
    return (stem or "download") + fp.suffix


def share_file_sync(fp: Path) -> str:
    """呼叫分享腳本建立臨時下載連結並回傳網址，失敗則拋出例外。"""
    script = Path(SHARE_SCRIPT)
    if not SHARE_SCRIPT or not script.exists():
        raise FileNotFoundError(f"找不到分享腳本 {SHARE_SCRIPT or '(未設定 SHARE_SCRIPT)'}")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(script), str(fp),
         "-As", ascii_name(fp), "-Hours", str(SHARE_HOURS)],
        capture_output=True, timeout=120,
    )
    # 刻意不解碼全文：PowerShell 的非英文輸出在管道裡是系統編碼（中文 Windows 是
    # CP950），解錯會誤判成失敗；網址是純 ASCII，直接在 bytes 上比對最穩。
    m = _SHARE_URL_RE.search(proc.stdout)
    if not m:
        raise RuntimeError("分享腳本沒有輸出連結")
    return m.group(0).decode("ascii")


async def share_file(fp: Path) -> str:
    """非同步版；建通道最久約 40 秒，留兩倍餘裕。"""
    return await asyncio.wait_for(asyncio.to_thread(share_file_sync, fp), timeout=150)


async def _share_big(channel: Any, fp: Path) -> None:
    """大檔改走臨時連結。沒設 SHARE_SCRIPT 就退回原本的「檔案太大」訊息。"""
    if not SHARE_SCRIPT:
        await channel.send(t("file_too_large", name=fp.name, fp=fp))
        return
    size = f"{fp.stat().st_size / 1024 / 1024:.1f} MB"
    note = await channel.send(t("file_sharing", name=fp.name, size=size))
    try:
        url = await share_file(fp)
    except Exception as e:  # noqa: BLE001 — 失敗要講給人聽，不能靜默
        await note.edit(content=t("file_share_failed", name=fp.name, fp=fp, e=e))
        return
    await note.edit(content=t("file_shared", name=fp.name, size=size, url=url, hours=SHARE_HOURS))


async def send_file(channel: Any, path: str | Path, note: str = "") -> None:
    """把一個檔案送進頻道。永不拋例外——傳檔失敗要變成一則訊息，不能拖垮渲染迴圈。"""
    fp = Path(path)
    try:
        if not fp.exists() or not fp.is_file():
            await channel.send(t("file_not_found", fp=fp))
            return
        caption = t("file_note", note=note.strip()) if note and note.strip() else None
        if fp.stat().st_size <= upload_limit(channel):
            try:
                await channel.send(content=caption, file=discord.File(str(fp)))
                return
            except discord.HTTPException as e:
                # 413：實際上限比我們算的更低，退而走下載連結；其餘錯誤照常回報
                if e.status != 413:
                    await channel.send(t("file_upload_failed", name=fp.name, e=e))
                    return
        if caption:
            await channel.send(caption)
        await _share_big(channel, fp)
    except Exception:  # noqa: BLE001
        log.exception("傳檔失敗 %s", fp)
