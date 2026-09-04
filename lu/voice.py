"""語音進出：轉錄使用者的語音訊息，開車模式時把回覆唸出來。

`drive_core` 是**選配**（要 faster-whisper ＋ F5-TTS ＋ CUDA），整包不在時這裡每個
函式都安全退場，語音訊息只會得到一句「語音模組沒裝」而不是讓回合失敗。

兩件事刻意分開，跟舊 cc-bot 一樣：
  **語音輸入永遠可用**——傳語音訊息就轉錄，不必先開什麼模式。
  **語音回覆要開車模式**（`/drive on`）——那才需要載 TTS、吃顯示卡記憶體。

實作全部在這裡；`commands/drive.py` 只負責那個 slash 指令。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from . import i18n
from .i18n import t
from .settings import BOT_DIR

log = logging.getLogger(__name__)

# 自訂參考音：F5-TTS 是零樣本聲音克隆，給固定的參考音＋逐字稿才會每次都同一個音色。
# 檔案不在就傳 None，drive_core 會改用套件內建的範例音。
REF_WAV = BOT_DIR / "f5_ref.wav"
REF_TEXT = "大家好，今天天氣很好，我們一起來聊聊最近發生的事情，希望你會喜歡這段內容。"

_state_file: Path | None = None
_tmp_dir: Path | None = None

try:
    import drive_core
except Exception:      # noqa: BLE001 — 刪掉 drive_core.py 即停用語音，其餘功能照常
    drive_core = None  # type: ignore[assignment]


def setup(data_dir: Path) -> None:
    """由 bootstrap 呼叫一次：狀態檔位置與暫存目錄。接線之前 `is_on()` 一律 False。"""
    global _state_file, _tmp_dir
    _state_file = Path(data_dir) / "drive_mode.json"
    _tmp_dir = Path(data_dir) / "tmp"
    _tmp_dir.mkdir(parents=True, exist_ok=True)


def available() -> bool:
    return drive_core is not None


def is_on() -> bool:
    """開車模式（語音回覆）開著嗎。語音**輸入**不看這個。"""
    if drive_core is None or _state_file is None:
        return False
    try:
        return bool(drive_core.load_drive(_state_file))
    except Exception:  # noqa: BLE001
        return False


def set_on(on: bool) -> None:
    """寫開關狀態；立刻落檔，重啟後自動恢復。"""
    if drive_core is not None and _state_file is not None:
        drive_core.save_drive(_state_file, on)


_last_use = 0.0
IDLE_UNLOAD_SEC = 600.0


async def reaper() -> None:
    """閒置太久就把 Whisper 從顯示卡上卸下來。

    使用者的顯示卡是拿來玩遊戲的。一則語音訊息之後模型會一直佔著 VRAM，
    十分鐘沒再用到就卸掉，下次要用再載（載入約兩秒，比一直佔著划算）。
    """
    import time
    while True:
        await asyncio.sleep(60)
        if drive_core is None or _last_use <= 0:
            continue
        if time.monotonic() - _last_use < IDLE_UNLOAD_SEC:
            continue
        try:
            drive_core.unload_whisper()
            drive_core.free_vram()
            log.info("Whisper 閒置 %.0f 秒，已卸載", IDLE_UNLOAD_SEC)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _mark_idle()


def _mark_idle() -> None:
    global _last_use
    _last_use = 0.0


async def transcribe(path: str | Path, hint: str = "") -> str:
    """音檔 → 逐字稿。模組不在或轉不出來回空字串，永不拋例外。

    跑在執行緒裡：faster-whisper 是同步的，直接呼叫會把事件迴圈卡住好幾秒，
    那期間**所有頻道**的畫面都會停住。
    """
    global _last_use
    if drive_core is None:
        return ""
    try:
        import time
        _last_use = time.monotonic()
        out = await asyncio.to_thread(drive_core.transcribe, str(path), hint or t("stt_prompt"))
        _last_use = time.monotonic()
        return (out or "").strip()
    except Exception:  # noqa: BLE001
        log.exception("轉錄失敗 %s", path)
        return ""


async def synthesize(text: str) -> Path | None:
    """文字 → 合成好的 .wav 路徑。模組不在、文字空的、或還沒接線回 None。"""
    if drive_core is None or _tmp_dir is None or not (text or "").strip():
        return None
    _tmp_dir.mkdir(parents=True, exist_ok=True)
    out = _tmp_dir / f"speak_{uuid.uuid4().hex[:8]}.wav"
    ref_wav = str(REF_WAV) if REF_WAV.is_file() else None
    ref_text = REF_TEXT if ref_wav else None
    lang = "zh" if i18n.BOT_LANG.startswith("zh") else "en"
    try:
        await asyncio.to_thread(drive_core.synthesize, text, str(out), lang, ref_wav, ref_text)
    except Exception:  # noqa: BLE001
        log.exception("語音合成失敗")
        return None
    return out if out.exists() else None


async def speak(reply: str) -> tuple[Path | None, str]:
    """回覆 →（語音檔, 唸出來的字）。沒開開車模式就回 (None, "")。

    `parse_speak` 把「要唸的那一段」抽出來——模型可以用標記指定只唸重點，
    不然一整篇文件唸下去沒人聽得完。
    """
    if not is_on():
        return None, ""
    say = strip = reply
    if drive_core is not None:
        try:
            marked, clean = drive_core.parse_speak(reply)
            say = marked if marked is not None else clean
            strip = clean
        except Exception:  # noqa: BLE001
            pass
    say = (say or "").strip()
    if not say:
        return None, ""
    return await synthesize(say), say


def strip_speak_marks(reply: str) -> str:
    """把朗讀標記從文字回覆裡拿掉——那是給合成器看的，不該出現在畫面上。"""
    if drive_core is None:
        return reply
    try:
        _say, clean = drive_core.parse_speak(reply)
        return clean or reply
    except Exception:  # noqa: BLE001
        return reply
