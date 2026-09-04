"""把一則 Discord 訊息收成一段給模型看的文字。

附件不進 prompt 本文，改成存到 `data/tmp/` 再把**路徑**寫進去——模型有這台電腦的
完整控制權，讀檔比把整份內容塞進 context 便宜得多，圖片也才有辦法給它看。
語音訊息這一批先只認出來並說明，轉錄在下一批（drive_core 還沒接上來）。
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import voice
from .i18n import t

log = logging.getLogger("lu")

# 附件單檔上限：超過就不下載，只寫檔名。25MB 是 Discord 免費上限，
# 真的需要更大的檔案，使用者自己放硬碟上再講路徑比較快。
MAX_ATTACH = 25 * 1024 * 1024

_MENTION_RE = re.compile(r"<@!?\d+>")
# 語音訊息在 Discord 上是 voice-message.ogg 這個固定檔名
_VOICE_NAMES = {"voice-message.ogg", "voice-message.mp3"}


def clean_mentions(text: str) -> str:
    """把 `<@123>` 拿掉。插話路徑先前漏了這一步，模型會看到一串裸 id。"""
    return _MENTION_RE.sub("", text or "").strip()


def sweep_tmp(tmp_dir: Path, max_age_h: int = 24) -> int:
    """清掉 tmp/ 裡過期的東西，回清掉幾個。**資料夾也要清**——舊 bot 只看 is_file()，
    六月的暫存目錄在硬碟上躺了兩個多月。"""
    import shutil
    if not tmp_dir.is_dir():
        return 0
    cutoff = time.time() - max_age_h * 3600
    n = 0
    for f in tmp_dir.iterdir():
        try:
            if f.stat().st_mtime >= cutoff:
                continue
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink()
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


async def collect(message: Any, tmp_dir: Path) -> tuple[str, bool]:
    """訊息 →（給模型的文字, 有沒有語音）。永不拋例外。

    回傳的文字是空字串時呼叫端該忽略這則訊息（只有貼圖、只有 @ 之類）。
    """
    parts: list[str] = []
    body = clean_mentions(message.content or "")
    if body:
        parts.append(body)
    has_voice = False
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for att in getattr(message, "attachments", None) or []:
        name = getattr(att, "filename", "") or "file"
        size = int(getattr(att, "size", 0) or 0)
        if name in _VOICE_NAMES:
            has_voice = True
        if size > MAX_ATTACH:
            parts.append(t("intake_too_big", name=name))
            continue
        fp = tmp_dir / f"{uuid.uuid4().hex[:8]}_{_safe(name)}"
        try:
            await att.save(fp)
        except Exception:  # noqa: BLE001 — 一個附件壞掉不該讓整則訊息消失
            log.exception("附件存檔失敗 %s", name)
            parts.append(t("intake_failed", name=name))
            continue
        if name in _VOICE_NAMES:
            # 語音**輸入永遠可用**，不必先開開車模式（那個只管語音回覆）
            said = await voice.transcribe(fp)
            parts.append(t("intake_voice", text=said) if said else t("voice_unavailable")
                         if not voice.available() else t("intake_voice_empty"))
            continue
        parts.append(t("intake_file", name=name, path=fp))
    return ("\n".join(p for p in parts if p).strip(), has_voice)


def _safe(name: str) -> str:
    """檔名只留安全字元，並擋掉路徑穿越（附件檔名是使用者可控的）。"""
    keep = "".join(c for c in Path(name).name if c.isalnum() or c in "._-")
    return keep[-60:] or "file"
