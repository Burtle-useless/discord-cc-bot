"""開車模式核心邏輯（本機語音：Whisper 語音轉文字 + XTTS 文字轉語音）。

與 Discord 無關的純邏輯，方便獨立測試，也讓「不需要語音的部署」能整包移除：
主程式對本模組採選配匯入（import 失敗即停用開車模式、降級為純文字 bot）。

概念：開車時把語音訊息轉文字（STT）、把 CC 回覆的朗讀版合成語音（TTS）；兩個模型
都延遲載入、可卸載釋放 VRAM。語音相關的重量級套件（faster-whisper、f5-tts、
torch）一律在函式內延遲匯入——沒安裝時，只有真的呼叫載入類函式才會 ImportError，
由呼叫端負責 try/except 降級；只做字串處理（parse_speak）或讀寫開關狀態
（load_drive/save_drive）則完全不需要那些套件。

授權提醒：F5-TTS 模型為 CC-BY-NC 4.0（禁止商用）。屬選配功能，只有開車模式會載入；
下游若要商用請改用其他引擎或自負授權責任。
"""
from __future__ import annotations

import gc
import importlib
import json
import os
import re
from pathlib import Path

# CC 回覆裡的朗讀版標記：<<<SPEAK>>> ... <<<ENDSPEAK>>>
_SPEAK_MARKER = re.compile(r"<<<SPEAK>>>(.*?)<<<ENDSPEAK>>>", re.DOTALL)


# ── GPU 共用工具（Whisper 與 F5-TTS 都會用到）─────────────────────────────
def add_cuda_dll_path() -> None:
    """把 nvidia cuda_runtime/cuBLAS/cuDNN/nvrtc 的 DLL 目錄加進 PATH，否則 GPU 推論時
    ctranslate2/torch 會找不到 cublas64_12.dll（它走 PATH，不吃 add_dll_directory）。"""
    dirs: list[str] = []
    for pkg in ("nvidia.cuda_runtime", "nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):
        try:
            mod = importlib.import_module(pkg)
            bindir = Path(mod.__path__[0]) / "bin"
            if bindir.is_dir():
                dirs.append(str(bindir))
        except Exception:
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


def free_vram() -> None:
    """強制回收記憶體與 GPU 顯存（卸載模型後呼叫）。"""
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass  # 純 CPU 環境沒裝 torch 就略過


# ── 語音轉文字（faster-whisper，本機 GPU，開車用）────────────────────────
_whisper_model = None  # 單例，只載入一次


def get_whisper():
    """延遲載入 Whisper 模型（單例）；首次會下載 large-v3 並載入 GPU。GPU 起不來自動退 CPU。"""
    global _whisper_model
    if _whisper_model is None:
        add_cuda_dll_path()
        from faster_whisper import WhisperModel
        try:
            # 8GB VRAM 跑 large-v3 float16 綽綽有餘
            _whisper_model = WhisperModel("large-v3", device="cuda", compute_type="float16")
            print("[WHISPER] large-v3 已載入 GPU", flush=True)
        except Exception as e:
            print(f"[WHISPER] GPU 失敗，改用 CPU：{e}", flush=True)
            _whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    return _whisper_model


def transcribe(path: str, initial_prompt: str = "") -> str:
    """把音檔轉成文字（阻塞式，呼叫端要用 asyncio.to_thread 包起來）。
    initial_prompt 由呼叫端提供（本模組不依賴主程式的 i18n）。"""
    model = get_whisper()
    # 不鎖語言（自動偵測，相容中英混講）；initial_prompt 可偏向特定語言
    segments, _info = model.transcribe(path, beam_size=5, initial_prompt=initial_prompt)
    return "".join(seg.text for seg in segments).strip()


def unload_whisper() -> None:
    """卸載 Whisper 模型、釋放顯存（開車模式關閉時呼叫）。"""
    global _whisper_model
    if _whisper_model is not None:
        _whisper_model = None
        free_vram()


# ── 文字轉語音（F5-TTS，本機 GPU，開車回覆用）─────────────────────────────
# F5-TTS 是零樣本聲音克隆模型，需要一段參考音 + 對應逐字稿。這裡直接用 f5-tts 套件自帶
# 的範例參考音（infer/examples/basic），因此本 repo 不必附任何音檔，也沒有授權疑慮。
_f5_model = None  # 單例，只載入一次
_f5_refs: dict[str, tuple[str, str]] = {}  # ref_lang -> (參考音路徑, 逐字稿)，延遲解析


def _f5_ref(ref_lang: str) -> tuple[str, str]:
    """依語系取得 F5 參考音（路徑, 逐字稿）；用套件內建範例，zh 用中文、其餘用英文。結果快取。"""
    key = "zh" if ref_lang == "zh" else "en"
    if key not in _f5_refs:
        import f5_tts
        base = Path(f5_tts.__file__).parent / "infer" / "examples" / "basic"
        if key == "zh":
            _f5_refs[key] = (str(base / "basic_ref_zh.wav"),
                             "对，这就是我，万人敬仰的太乙真人。")
        else:
            _f5_refs[key] = (str(base / "basic_ref_en.wav"),
                             "Some call me nature, others call me mother nature.")
    return _f5_refs[key]


def get_f5tts():
    """延遲載入 F5-TTS（單例）；首次會下載 F5TTS_v1_Base 約 1.3GB。GPU 起不來自動退 CPU。"""
    global _f5_model
    if _f5_model is None:
        add_cuda_dll_path()
        from f5_tts.api import F5TTS
        try:
            _f5_model = F5TTS(device="cuda")
            print("[F5] F5TTS_v1_Base 已載入 GPU", flush=True)
        except Exception as e:
            print(f"[F5] GPU 失敗，改用 CPU：{e}", flush=True)
            _f5_model = F5TTS(device="cpu")
    return _f5_model


def synthesize(text: str, out_path: str, ref_lang: str = "en") -> str:
    """把文字合成成語音檔（阻塞式，呼叫端用 asyncio.to_thread 包起來）。回傳檔案路徑。
    ref_lang 由呼叫端依介面語系決定（zh/en），選對應的內建參考音（本模組不依賴主程式的 i18n）。"""
    model = get_f5tts()
    ref_audio, ref_text = _f5_ref(ref_lang)
    # F5-TTS 零樣本克隆：用套件自帶參考音 + 逐字稿，生成語言由 text 內容自動判定
    model.infer(
        ref_file=ref_audio,
        ref_text=ref_text,
        gen_text=text,
        file_wave=out_path,
        remove_silence=False,
    )
    return out_path


def unload_f5tts() -> None:
    """卸載 F5-TTS 模型、釋放顯存（開車模式關閉時呼叫）。"""
    global _f5_model
    if _f5_model is not None:
        _f5_model = None
        free_vram()


# ── 朗讀標記抽取（純字串處理，不需任何語音套件）───────────────────────────
def parse_speak(reply: str) -> tuple[str | None, str]:
    """從 CC 回覆抽出朗讀版標記 <<<SPEAK>>>...<<<ENDSPEAK>>>。

    回傳 (朗讀文字, 去標記後的乾淨文字)：
    - 沒有標記 → (None, 原文)，呼叫端可原樣送出。
    - 有標記 → (標記內文字去空白後的結果, 去標記文字)；朗讀文字可能是空字串。
    """
    m = _SPEAK_MARKER.search(reply)
    if not m:
        return None, reply
    clean = _SPEAK_MARKER.sub("", reply).strip()
    return m.group(1).strip(), clean


# ── 開車開關狀態持久化（純檔案 IO）───────────────────────────────────────
def load_drive(path: Path) -> bool:
    """讀開車模式狀態：檔案優先，預設 False。"""
    try:
        return bool(json.loads(Path(path).read_text()).get("drive"))
    except Exception:
        return False


def save_drive(path: Path, on: bool) -> None:
    """把開車模式開關寫檔（失敗靜默略過，不影響指令流程）。"""
    try:
        Path(path).write_text(json.dumps({"drive": on}))
    except Exception:
        pass
