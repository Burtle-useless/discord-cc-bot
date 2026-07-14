"""Discord bot — 本地跑，橋接 Claude Code CLI。"""

import asyncio
import random
import traceback
import json
import os
import re
import time
import uuid
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    AssistantMessage,
    ToolUseBlock,
    StreamEvent,
    HookMatcher,
)
from claude_agent_sdk._errors import MessageParseError
from claude_agent_sdk._internal.message_parser import parse_message

import wt_core  # 本地模組：git worktree 平行協作核心邏輯
import coord_core  # 本地模組：跨頻道協作（AI Lounge）核心邏輯
try:
    import drive_core  # 本地模組：開車模式（本機語音 STT/TTS）核心邏輯；選配、可整包移除
except ImportError:
    drive_core = None  # 刪掉 drive_core.py 即停用開車模式，bot 自動降級為純文字

try:
    from croniter import croniter as _croniter
    _HAS_CRONITER = True
except ImportError:
    _HAS_CRONITER = False

# 從 .env 載入設定（沒裝 python-dotenv 時，退而仰賴系統環境變數）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── 介面語言（i18n）：字串集中在 i18n.py（en / zh-TW）─────────────────────
from i18n import BOT_LANG, t

_NO_RESPONSE = t("no_response")   # run_claude 無輸出時的哨兵字串（多處比對用同一個值）

# SDK 的 initialize timeout 由此環境變數控制（最低 60s）。健康時 init 僅約 1.2s，
# 設 60s 讓異常卡死能較快失敗、進入重試，而非乾等 3 分鐘
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "60000")

# ── 設定（從 .env 讀取，第一次使用請複製 .env.example 為 .env 並填入）───────
def _require_env(key: str) -> str:
    """讀取必填環境變數；缺少時拋出清楚的錯誤，引導使用者去設定 .env。"""
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(t("require_env", key=key))
    return val

DISCORD_TOKEN   = _require_env("DISCORD_TOKEN")
# CLAUDE_CLI 選填，預設指向 npm 全域安裝的 claude.cmd（%APPDATA%\npm\claude.cmd）
CLAUDE_CLI      = os.environ.get("CLAUDE_CLI") or os.path.expandvars(r"%APPDATA%\npm\claude.cmd")
# DEFAULT_DIR 選填，預設為使用者家目錄
DEFAULT_DIR     = Path(os.environ.get("DEFAULT_DIR") or Path.home())
MAX_MSG        = 1900
USAGE_CACHE_SEC = 180
ALLOWED_USER    = int(_require_env("ALLOWED_USER"))
# 跨頻道協作（AI Lounge）：開啟後各頻道 session 會在 prompt 收到其他頻道的近期活動，
# 並可用 [[COORD: ...]] 廣播。預設關閉 → 行為與未啟用時完全相同（零影響）。
COORD_ENABLED   = (os.environ.get("COORD_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")
COORD_CHANNEL   = int(os.environ.get("COORD_CHANNEL") or 0)   # 協作廣播頻道（選填，0=只更新登錄表、不發頻道）
_coord_registry = coord_core.Registry()   # 記憶體版「頻道→近期活動」登錄表（單例）
# 側欄分類／入口頻道名稱（選填）。同一個伺服器要跑多個 bot 時，各 bot 設不同的
# SIDEBAR_CATEGORY 就不會互搶同一個分類（否則兩個 bot 會一起搶「CC 對話」而衝突）。
SIDEBAR_CATEGORY = os.environ.get("SIDEBAR_CATEGORY") or t("sidebar_category_default")   # 多 session 側欄的分類容器名
SIDEBAR_ENTRY    = os.environ.get("SIDEBAR_ENTRY") or t("sidebar_entry_default")           # 側欄最上方的入口頻道名（放常駐按鈕）
FALLBACK_MODEL  = "claude-sonnet-4-6"      # 主模型過載時的備援模型
# DEFAULT_MODEL 選填：新對話的預設模型。預設用標準 200K context 的 Sonnet，
# 所有方案都能跑（不需 1M context credits）。若你的方案有 1M credits 想啟用，
# 改成對應的 1M 模型別名（例如 claude-sonnet-4-6[1m]），壓縮門檻會自動跟著放大。
DEFAULT_MODEL   = os.environ.get("DEFAULT_MODEL") or "claude-sonnet-4-6"
MAX_BUFFER_SIZE = 64 * 1024 * 1024         # stream-json 解析 buffer 上限（64MB）
RETRY_MAX_ATTEMPTS = 4                       # 529/429 退避重試次數上限
RETRY_BASE_DELAY   = 1.0                     # 退避基礎秒數
# ── context 視窗上限：依「模型 + 帳號方案」套用 Anthropic 官方規則 ───────────
# 官方規則（Claude Code: Model configuration）：
#   1) 模型別名含 [1m]（例 claude-sonnet-4-6[1m]）→ 強制 1M；帳號若無資格 CC 會自己擋。
#   2) Opus 在 Max／Team／Enterprise 方案 → 訂閱內建、自動升 1M，免額外設定。
#   3) 其餘（Sonnet 任何方案、Opus 在 Pro、不支援 1M 的舊模型）→ 標準 200K。
#      這些情況要用 1M 需另購 usage credits，做法就是加 [1m] 後綴（落回規則 1）。
# 自動壓縮門檻＝上限的 85%（1M→850k、200K→170k）；實際數值在各呼叫點用 _ctx_limit() 算。
_AUTO_1M_PLANS = frozenset({"max", "team", "enterprise"})   # Opus 在這些方案自動 1M

def _is_opus(model: str) -> bool:
    """是否為 Opus 系列（本 bot 提供的 Opus 皆為支援 1M 的 4.6+）。"""
    return "opus" in (model or "").lower()

def context_limit_for(model: str, plan: str) -> int:
    """回傳該模型在該方案下的 context 視窗上限（tokens），套用上述官方規則。"""
    m = model or DEFAULT_MODEL or ""
    if "[1m]" in m:                                  # 規則 1：明確要 1M
        return 1_000_000
    if _is_opus(m) and plan in _AUTO_1M_PLANS:       # 規則 2：Opus 在 Max/Team/Enterprise 自動 1M
        return 1_000_000
    return 200_000                                   # 規則 3：其餘標準 200K
NOTIFY_AFTER_SEC = 60                        # 任務耗時超過此秒數，完成時 @使用者推播
INACTIVITY_TIMEOUT = 600                      # CC 連續無任何輸出超過此秒數才視為卡死（不限總時長，長工作流不會被誤殺）

# 資料檔案放在 bot/ 同目錄下
_BOT_DIR        = Path(__file__).parent
_SESSION_FILE   = _BOT_DIR / "discord_session.json"
_USERS_FILE     = _BOT_DIR / "allowed_users.json"
_SCHEDULES_FILE = _BOT_DIR / "schedules.json"
_TITLES_FILE    = _BOT_DIR / "session_titles.json"   # session_id → 自訂/AI 生成的中文標題
_VECTORS_FILE   = _BOT_DIR / "session_vectors_e5.json"  # session_id → {mtime, vecs:[[...]]}，/search 多塊向量快取（e5）
_USER_LEDGER_FILE = _BOT_DIR / "user_messages.jsonl"  # 使用者原始訊息帳本（append-only，供查證，勿併入 git）


def _append_user_ledger(channel_id: int, author: str, text: str) -> None:
    """把使用者原始訊息 append 到帳本檔（append-only、不受 AI 壓縮影響），
    作為日後查證『使用者到底說過什麼』的第一手來源。永不因寫檔失敗中斷主流程。"""
    try:
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "channel_id": channel_id, "author": author, "text": text}
        with _USER_LEDGER_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

@dataclass(slots=True)
class ChannelState:
    """一個頻道的會話狀態（原為 stringly-typed dict、欄位散落全檔）。

    slots=True：欄位名打錯（讀或寫）都立刻 AttributeError（fail-loud），
    不再像 dict 靜默回 None 或長出殭屍鍵（ctx_warned 事件的根因）。
    底線開頭為執行期旗標，不進 _persist_session 的持久化內容。"""
    _cid: int                                    # 所屬頻道 id
    cwd: Path                                    # 工作目錄
    session_id: Optional[str] = None             # CC session id（None＝尚未開始）
    model: Optional[str] = None                  # 指定模型（None＝帳號預設）
    effort: Optional[str] = None                 # 思考程度（None＝預設）
    wt: Optional[dict] = None                    # worktree 資訊（path/branch/base/repo/prev_cwd）
    ctx_tokens: int = 0                          # 最近一次 result 回報的 context 用量
    pending_options: Optional[list[str]] = None  # AskUserQuestion 待答選項（數字回覆對應用）
    _session_label: Optional[str] = None         # 顯示用標題快取
    _sidebar: bool = False                       # 是否為側欄對話頻道（記憶體旗標）
    _named: bool = False                         # 側欄頻道是否已完成命名（防重複自動改名）

# ── 允許使用者持久化 ────────────────────────────────────────────────────
def _load_allowed_users() -> set[int]:
    try:
        data = json.loads(_USERS_FILE.read_text())
        return {ALLOWED_USER} | set(data)
    except Exception:
        return {ALLOWED_USER}

def _save_allowed_users(users: set[int]) -> None:
    try:
        _USERS_FILE.write_text(json.dumps(list(users)))
    except Exception:
        pass

_allowed_users: set[int] = _load_allowed_users()

# ── 帳號方案持久化（決定 Opus 是否自動 1M context）──────────────────────────
# 方案是「整個帳號層級」設定（同一個 bot＝同一組 Claude 登入），故全域存一份、不分
# session。用 /plan 指令設定；也可用環境變數 CLAUDE_PLAN 當預設值。
_PLAN_FILE = _BOT_DIR / "account_plan.json"

def _load_plan() -> str:
    """讀帳號方案：檔案優先，其次環境變數，最後 'unknown'（行為等同舊版：只認 [1m]）。"""
    try:
        v = json.loads(_PLAN_FILE.read_text()).get("plan")
        if v:
            return v
    except Exception:
        pass
    return os.environ.get("CLAUDE_PLAN") or "unknown"

def _save_plan(plan: str) -> None:
    try:
        _PLAN_FILE.write_text(json.dumps({"plan": plan}))
    except Exception:
        pass

_account_plan: str = _load_plan()

def _ctx_limit(state: ChannelState) -> int:
    """該 session 目前的 context 上限：依其模型（未設則用 DEFAULT_MODEL）＋帳號方案。"""
    return context_limit_for(state.model or DEFAULT_MODEL, _account_plan)

# ── 開車模式持久化（語音輸入↔語音回覆的總開關）──────────────────────────────
# 開車時 /drive on 載入 Whisper+XTTS、啟用「語音進→語音出」；在家 /drive off 全部卸載、
# 釋放 VRAM，回到純文字 bot。全域設定（同一個 bot 一個開關），仿帳號方案存一份。
# 預設 False（在家、不吃效能）。
_DRIVE_FILE = _BOT_DIR / "drive_mode.json"

# 開車開關狀態的讀寫委派給 drive_core（純檔案 IO）；模組缺席時一律視為關閉。
_drive_mode: bool = drive_core.load_drive(_DRIVE_FILE) if drive_core else False

# ── 允許頻道持久化（可加開多個頻道並行跑工作）──────────────────────────────
_CHANNELS_FILE = _BOT_DIR / "allowed_channels.json"

def _load_allowed_channels() -> set[int]:
    # 授權頻道純由 allowed_channels.json 持久化；不存在就回空集合，
    # 開機後由 on_ready 的 _ensure_sidebar 掃描分類把既有對話頻道補回來
    try:
        return {int(x) for x in json.loads(_CHANNELS_FILE.read_text())}
    except Exception:
        return set()

def _save_allowed_channels(chs: set[int]) -> None:
    try:
        _CHANNELS_FILE.write_text(json.dumps(list(chs)))
    except Exception:
        pass

_allowed_channels: set[int] = _load_allowed_channels()

# ── 排程持久化 ──────────────────────────────────────────────────────────
def _load_schedules() -> list[dict]:
    try:
        return json.loads(_SCHEDULES_FILE.read_text())
    except Exception:
        return []

def _save_schedules(schedules: list[dict]) -> None:
    try:
        _SCHEDULES_FILE.write_text(json.dumps(schedules, ensure_ascii=False, indent=2))
    except Exception:
        pass

# ── 錯誤分類 ────────────────────────────────────────────────────────────
def classify_cc_error(err: str) -> str:
    s = err.lower()
    if "exceeded maximum buffer size" in s or "failed to decode json" in s:
        return "INPUT_TOO_LARGE"
    if "prompt is too long" in s or ("400" in s and "too long" in s):
        return "CONTEXT_FULL"
    if "529" in s or "overloaded" in s:
        return "OVERLOADED"
    if "429" in s or "rate_limit" in s or "rate limit" in s:
        return "RATE_LIMIT"
    if "failed to start claude" in s or "winerror 267" in s or "目錄名稱無效" in s:
        return "STARTUP"
    if "401" in s or "credential" in s or "authentication" in s or "unauthorized" in s:
        return "AUTH"
    if "control request timeout" in s or "initialize" in s:
        return "INIT_TIMEOUT"
    return "UNKNOWN"

USER_FACING = {
    "INPUT_TOO_LARGE": t("err_input_too_large"),
    "CONTEXT_FULL":    t("err_context_full"),
    "OVERLOADED":      t("err_overloaded"),
    "RATE_LIMIT":      t("err_rate_limit"),
    "AUTH":            t("err_auth"),
    "STARTUP":         t("err_startup"),
    "TIMEOUT":         t("err_timeout"),
    "INIT_TIMEOUT":    t("err_init_timeout"),
    "UNKNOWN":         t("err_unknown"),
}

_RETRYABLE    = {"OVERLOADED", "RATE_LIMIT", "INIT_TIMEOUT"}
_RESET_SESSION = {"CONTEXT_FULL"}


class CCError(Exception):
    """已分類的 CC 錯誤，帶 kind 與給使用者看的訊息。"""
    def __init__(self, kind: str, raw: str):
        self.kind = kind
        self.raw = raw
        self.user_msg = USER_FACING.get(kind, USER_FACING["UNKNOWN"])
        super().__init__(f"{kind}: {raw[:200]}")


# ── Anthropic 平台 incident 偵測 ────────────────────────────────────────
_incident_cache: dict = {}
_INCIDENT_CACHE_SEC = 120

def is_incident_active() -> Optional[str]:
    now = time.time()
    if "ts" in _incident_cache and now - _incident_cache["ts"] < _INCIDENT_CACHE_SEC:
        return _incident_cache.get("title")
    title = None
    try:
        req = urllib.request.Request(
            "https://status.claude.com/history.atom",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            raw = r.read().decode("utf-8", errors="replace")
        entries = re.findall(r"<entry>(.*?)</entry>", raw, re.DOTALL)[:5]
        for e in entries:
            m = re.search(r"<title[^>]*>(.*?)</title>", e, re.DOTALL)
            txt = (m.group(1) if m else "").strip()   # 不叫 t：避免遮蔽 i18n 的 t()
            body = e.lower()
            if txt and "resolved" not in body and any(
                k in body for k in ("elevated", "outage", "degraded", "error")
            ):
                title = txt
                break
    except Exception as ex:
        print(f"[STATUS_CHECK] failed: {ex}", flush=True)
    _incident_cache["ts"] = now
    _incident_cache["title"] = title
    return title

# ── Session 持久化（per-channel，兩個頻道各記各的，不互相覆蓋）──────────────
def _load_sessions_map() -> dict:
    try:
        return json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _persist_session(state: ChannelState) -> None:
    cid = state._cid
    if cid is None:
        return
    try:
        data = _load_sessions_map()
        # 整包存：session_id + model/effort/cwd，重啟後設定不會變回預設
        data[str(cid)] = {
            "session_id": state.session_id,
            "model": state.model,
            "effort": state.effort,
            "cwd": str(state.cwd or DEFAULT_DIR),
            "wt": state.wt,   # worktree 模式資訊（None 表示未啟用）
        }
        _SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

_sessions: dict[int, ChannelState] = {}

_processing: dict[int, bool] = {}
_running_tasks: dict[int, "asyncio.Task"] = {}

# 長駐 ClaudeSDKClient 池（A'）：每頻道維持一個活進程，活躍期間不關＝桌面同邏輯、
# 同進程同 session、不 fork、不留碎片；閒置逾時才回收釋放記憶體，下次以 resume 接回。
_clients: dict[int, "ClaudeSDKClient"] = {}
_client_used: dict[int, float] = {}        # 每頻道 client 最後使用時間（閒置回收判斷用）
_client_sigs: dict[int, tuple] = {}        # 每頻道 client 的設定指紋（變了就重建）
CLIENT_IDLE_TIMEOUT = 900                  # 閒置逾時（秒）：超過則回收該頻道 client


class _StoppedByUser(Exception):
    """使用者透過 /stop 主動中止工作。"""


_MILESTONE_RE = re.compile(r'\[\[MILESTONE:\s*(.+?)\]\]')


async def _run_tracked(
    channel_id: int,
    prompt: str,
    state: ChannelState,
    progress_msg: Optional[discord.Message],
) -> tuple[str, Optional[str], Optional[dict]]:
    """把 run_claude 包成可取消的 task，登記到 _running_tasks 供 /stop 使用。"""
    # 協作啟用時：把其他頻道近期活動摘要注入 prompt 最前面（只在對話主流程；
    # compact／語音解析不經過本函式，因此天然不受影響）。
    if COORD_ENABLED:
        feed = _coord_registry.format_for_prompt(exclude=channel_id)
        if feed:
            prompt = t("coord_prompt_prefix", feed=feed) + prompt
    last_kind = "UNKNOWN"
    last_raw = ""
    for attempt in range(RETRY_MAX_ATTEMPTS):
        task = asyncio.create_task(run_claude(prompt, state, progress_msg))
        _running_tasks[channel_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            raise _StoppedByUser()
        except (asyncio.TimeoutError, TimeoutError):
            raise CCError("TIMEOUT", "inactivity timeout（連續無輸出）")
        except Exception as e:
            raw = str(e).strip() or (type(e).__name__ + t("no_message"))
            tb = traceback.format_exc()
            kind = classify_cc_error(raw + " " + tb)
            last_kind, last_raw = kind, raw
            print(f"[CC_FAIL] kind={kind} attempt={attempt+1}/{RETRY_MAX_ATTEMPTS} "
                  f"type={type(e).__name__} raw={raw!r}\n{tb}", flush=True)
            if kind not in _RETRYABLE:
                raise CCError(kind, raw)
            if attempt < RETRY_MAX_ATTEMPTS - 1:
                # INIT_TIMEOUT：前一個 CC 進程可能還沒釋放資源，多等 3 秒
                extra = 10.0 if kind == "INIT_TIMEOUT" else 0.0
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5) + extra
                if progress_msg:
                    try:
                        await progress_msg.edit(
                            content=t("retry_notice", kind=kind, delay=f"{delay:.0f}",
                                      n=attempt + 1, max=RETRY_MAX_ATTEMPTS))
                    except Exception:
                        pass
                await asyncio.sleep(delay)
        finally:
            _running_tasks.pop(channel_id, None)
    raise CCError(last_kind, last_raw)

# ── 每個 channel 的 session 狀態 ──────────────────────────────────────
_usage_cache: dict = {}

def get_state(cid: int) -> ChannelState:
    if cid not in _sessions:
        rec = _load_sessions_map().get(str(cid))
        if isinstance(rec, str):      # 舊格式：值只是 session_id 字串
            rec = {"session_id": rec}
        rec = rec or {}
        cwd = Path(rec.get("cwd")) if rec.get("cwd") else DEFAULT_DIR
        # worktree 模式：若紀錄的 worktree 目錄已被外部移除，視為未啟用並還原到啟用前的目錄
        wt_rec = rec.get("wt")
        if wt_rec and isinstance(wt_rec, dict) and not Path(wt_rec.get("path", "")).is_dir():
            prev = wt_rec.get("prev_cwd")
            if prev and Path(prev).is_dir():
                cwd = Path(prev)
            wt_rec = None
        _sessions[cid] = ChannelState(
            _cid=cid,
            cwd=cwd if cwd.is_dir() else DEFAULT_DIR,
            session_id=rec.get("session_id"),
            # model 欄位存在（含明確的 None＝使用者選過「帳號預設」）就照存的用；
            # 只有全新頻道／舊格式紀錄（無此欄位）才給 DEFAULT_MODEL——
            # 否則 /model 選「預設」後一重啟就被悄悄改回 DEFAULT_MODEL，前後行為不一致
            model=rec.get("model") if "model" in rec else DEFAULT_MODEL,
            effort=rec.get("effort"),
            wt=wt_rec if isinstance(wt_rec, dict) else None,
        )
    return _sessions[cid]

# ── 長駐 client（A'）輔助函式 ───────────────────────────────────────────
def _build_options(state: ChannelState) -> ClaudeAgentOptions:
    """依頻道狀態組出 ClaudeAgentOptions（建立長駐 client 時用一次）。"""
    # cwd 防護：切換歷史 session 可能帶入不存在的目錄（WinError 267），退回預設目錄
    if not Path(state.cwd).is_dir():
        state.cwd = DEFAULT_DIR
    options = ClaudeAgentOptions(
        cwd=str(state.cwd),
        cli_path=CLAUDE_CLI,
        model=state.model,
        effort=state.effort,
        # 維持全放行（自動執行，不干擾工作流）。危險指令確認改掛 PreToolUse hook：
        # 實測 headless/SDK 下 can_use_tool 回呼不會被觸發，但 PreToolUse hook 即使在
        # bypassPermissions 也照樣觸發、且 permissionDecision="deny" 能真正擋下工具。
        # 安全動作 hook 回空 dict 放行、破壞性指令才跳確認按鈕。
        permission_mode="bypassPermissions",
        hooks={"PreToolUse": [HookMatcher(matcher="Bash|PowerShell|AskUserQuestion",
                                          hooks=[_make_pretool_hook(state)])]},
        fallback_model=FALLBACK_MODEL,
        max_buffer_size=MAX_BUFFER_SIZE,
        # 此 system_prompt 會透過管線傳給 CC 子進程。實測在 Windows 上，內含 LaTeX
        # 錢字號、反引號、或無法用系統編碼表示的 Unicode 數學符號時，會讓 init 的控制
        # 訊息損毀，導致 initialize 卡死。故一律「用文字描述規則」，不嵌入危險字元本身。
        # 啟用協作時附加 coord_rule、開車模式附加 drive_rule（皆不含危險字元）。
        system_prompt=t("system_prompt")
        + (t("coord_rule") if COORD_ENABLED else "")
        + (t("drive_rule") if (_drive_mode and drive_core) else ""),
    )
    # 開啟逐字串流，讓生成中的回應能即時顯示在「思考中」訊息，提供存活訊號
    options.include_partial_messages = True
    return options


async def _drop_client(cid: int) -> None:
    """關閉並移除某頻道的長駐 client（出錯、/new、改設定、頻道刪除時用，永不拋例外）。"""
    c = _clients.pop(cid, None)
    _client_used.pop(cid, None)
    _client_sigs.pop(cid, None)
    if c is not None:
        try:
            await c.disconnect()
        except Exception:
            pass


def _client_sig(state: ChannelState) -> tuple:
    """長駐 client 的設定指紋（不含 session_id：sid 會在正常對話中由 client 自己產出）。
    cwd／model／effort／開車模式／協作模式任一改變，即代表要用新設定重建 client。"""
    return (str(state.cwd), state.model, state.effort,
            _drive_mode, COORD_ENABLED)


async def _acquire_client(state: ChannelState) -> "ClaudeSDKClient":
    """取得該頻道的長駐 client；無、或設定指紋已變，則（丟棄後）新建並連線。
    首次連線才帶 resume 接回舊 session；之後同一 client 多輪都在同進程同 session。"""
    cid = state._cid
    c = _clients.get(cid)
    if c is not None and _client_sigs.get(cid) == _client_sig(state):
        _client_used[cid] = time.time()
        return c
    if c is not None:                 # 設定已變 → 丟棄舊 client，用新設定重建
        await _drop_client(cid)
    options = _build_options(state)
    if state.session_id:
        options.resume = state.session_id   # 僅首次連線需要接回
    c = ClaudeSDKClient(options)
    await c.connect()
    _clients[cid] = c
    _client_sigs[cid] = _client_sig(state)
    _client_used[cid] = time.time()
    return c


def _resolve_project_dir(project_path: str) -> tuple[Path, bool]:
    """回傳 (可用目錄, 是否為原路徑)。原路徑不存在時退回預設目錄。"""
    p = Path(project_path)
    if p.is_dir():
        return p, True
    return DEFAULT_DIR, False

# ── 工具圖示 ───────────────────────────────────────────────────────────
_ICONS = {
    "Read":"📖","Write":"✏️","Edit":"✏️","MultiEdit":"✏️",
    "Bash":"⚙️","PowerShell":"⚙️","Glob":"🔍","Grep":"🔍",
    "WebSearch":"🌐","WebFetch":"🌐","TodoWrite":"📝",
    "Agent":"🤖","Task":"📋","ToolSearch":"🛠️",
    "TaskCreate":"📋","TaskUpdate":"📋","TaskList":"📋",
    "AskUserQuestion":"❓",
}

# 危險動作：會改檔案或執行系統指令的工具，在思考訊息裡要醒目標出並顯示完整內容
_DANGER_TOOLS = {"Bash", "PowerShell", "Write", "Edit", "MultiEdit"}

def _fmt_tool(name: str, inp: dict) -> str:
    icon = _ICONS.get(name, "🔧")
    danger = name in _DANGER_TOOLS
    if name in ("Write", "Edit", "MultiEdit") and "file_path" in inp:
        detail = inp["file_path"]                          # 改檔：顯示完整路徑，看清動的是哪個檔
    elif name in ("Read", "Glob") and "file_path" in inp:
        detail = Path(inp["file_path"]).name
    elif name in ("Bash", "PowerShell") and "command" in inp:
        detail = inp["command"][:120].replace("\n", " ")   # 執行指令：顯示更完整的指令內容
    elif name == "Grep" and "pattern" in inp:
        detail = inp["pattern"][:50]
    elif name == "WebSearch" and "query" in inp:
        detail = inp["query"][:60]
    else:
        detail = str(list(inp.values())[0])[:60] if inp else ""
    detail = detail.replace("`", "'")                      # 反引號會破壞 Discord 標記，換掉
    if danger:                                             # 危險動作用 ⚠️ 標出，讓使用者一眼分辨
        return f"⚠️ {icon} **{name}**  `{detail}`" if detail else f"⚠️ {icon} **{name}**"
    return f"{icon} **{name}**  `{detail}`" if detail else f"{icon} **{name}**"

# ── 危險指令確認（第二階段）──────────────────────────────────────────────
# 逾時秒數（逾時＝取消不執行）。刻意小於 INACTIVITY_TIMEOUT，確認期間才不會被誤判卡死。
_CONFIRM_TIMEOUT_SEC = 300
# 危險確認總開關：預設關（使用者要求）。可用環境變數 CONFIRM_DANGEROUS=1 開啟，或執行時用 /confirm on 動態切換。
_CONFIRM_ENABLED = (os.environ.get("CONFIRM_DANGEROUS") or "0").strip() == "1"
# 破壞性指令樣式（大小寫不敏感、以詞界比對避免 confirm 之類誤判）。只攔最常見的高風險操作，
# 命中才跳確認按鈕、其餘一律放行——這是「確認」而非硬性沙箱，可視需要增修這份清單。
_DESTRUCTIVE_RE = re.compile(
    r"(?:\brm\s+-[rf]|\brmdir\b|\brd\s+/s|\bdel\s+/|\berase\s+/|"
    r"\bremove-item\b|\bformat\s|\bmkfs\b|\bdd\s+if=|"
    r"\bgit\s+push\b|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-|\bgit\s+checkout\s+--\s|\bgit\s+branch\s+-D\b|"
    r"\bshutdown\b|\brestart-computer\b|\bstop-computer\b|\btaskkill\b|\bstop-process\b|\bstop-service\b|"
    r"\breg\s+delete\b|\bdiskpart\b|\bsc\s+delete\b|\bcipher\s+/w)",
    re.IGNORECASE,
)


def _needs_confirm(tool_name: str, tool_input: dict) -> bool:
    """判斷這次工具呼叫是否為需確認的破壞性動作。只比對會執行任意系統指令的
    Bash/PowerShell（惡意夾帶／幻象指令的主要途徑）。判斷出錯時放行，不阻斷 CC。"""
    if not _CONFIRM_ENABLED:            # 總開關關閉時一律放行（由 /confirm 或 CONFIRM_DANGEROUS 控制）
        return False
    try:
        if tool_name in ("Bash", "PowerShell"):
            return bool(_DESTRUCTIVE_RE.search(str(tool_input.get("command", ""))))
    except Exception:
        return False
    return False


async def _confirm_dangerous(channel: discord.TextChannel, tool_name: str,
                             tool_input: dict) -> Optional[bool]:
    """送出危險動作確認按鈕並等待回應。回傳 True=執行／False=取消／None=逾時。永不拋例外。"""
    detail = _fmt_tool(tool_name, tool_input)
    mins = max(1, _CONFIRM_TIMEOUT_SEC // 60)
    prompt = t("confirm_prompt", detail=detail, mins=mins)
    result: dict = {"v": None}
    view = discord.ui.View(timeout=_CONFIRM_TIMEOUT_SEC)
    sent: dict = {"msg": None}

    async def _click(inter: discord.Interaction, ok: bool) -> None:
        result["v"] = ok
        for it in view.children:
            it.disabled = True
        note = t("confirm_done_exec") if ok else t("confirm_done_cancel")
        try:
            await inter.response.edit_message(content=prompt + "\n\n" + note, view=view)
        except Exception:
            try:
                await inter.response.defer()
            except Exception:
                pass
        view.stop()

    async def _on_timeout() -> None:
        for it in view.children:
            it.disabled = True
        m = sent.get("msg")
        if m is not None:
            try:
                await m.edit(view=view)
            except Exception:
                pass

    view.on_timeout = _on_timeout
    ok_btn = discord.ui.Button(label=t("confirm_exec"), style=discord.ButtonStyle.danger)
    no_btn = discord.ui.Button(label=t("confirm_cancel"), style=discord.ButtonStyle.secondary)

    async def _ok(inter: discord.Interaction) -> None:
        await _click(inter, True)

    async def _no(inter: discord.Interaction) -> None:
        await _click(inter, False)

    ok_btn.callback = _ok
    no_btn.callback = _no
    view.add_item(ok_btn)
    view.add_item(no_btn)
    try:
        sent["msg"] = await channel.send(prompt, view=view)
        await view.wait()
    except Exception:
        pass
    return result["v"]


def _deny_hook(reason: str) -> dict:
    """組出 PreToolUse hook 的拒絕回應（deny 會讓 CC 收到 reason、不執行該工具）。"""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _make_pretool_hook(state: ChannelState):
    """為某頻道產生 PreToolUse hook（綁定該頻道 id）。非破壞性動作回空 dict（放行、
    不干擾自動化工作流）；破壞性指令送 Discord 確認按鈕，取消或逾時則回 deny 擋下並
    告知 CC 停手詢問使用者。用 hook 而非 can_use_tool，因後者在 headless/SDK 下不會被觸發。"""
    cid = state._cid

    async def _hook(input_data: dict, tool_use_id: Optional[str],
                    context: object) -> dict:
        tool_name = input_data.get("tool_name", "") if isinstance(input_data, dict) else ""
        tool_input = input_data.get("tool_input", {}) if isinstance(input_data, dict) else {}
        # AskUserQuestion：headless/SDK 下 CLI 無前端可顯示，內建工具會立刻回假 error，害 CC
        # 誤判工具失敗而搶答。改由 hook 攔下 deny（bot 另從串流的 ToolUseBlock 攔問題內容畫
        # Discord 按鈕），deny reason 明確告知 CC：問題已送達使用者、本回合到此為止、停止輸出
        # 等下一則訊息。deny 不影響 ToolUseBlock 出現在串流，按鈕流程照舊。
        if tool_name == "AskUserQuestion":
            return _deny_hook(t("ask_delegated_reason"))
        if not _needs_confirm(tool_name, tool_input):
            return {}                                      # 非破壞性：放行，不干擾自動化工作流
        channel = bot.get_channel(cid)
        if channel is None:                                # 找不到頻道無從確認，為安全起見擋下
            return _deny_hook(t("confirm_no_channel"))
        decision = await _confirm_dangerous(channel, tool_name, tool_input)
        if decision is True:
            return {}
        if decision is False:
            return _deny_hook(t("confirm_deny_cancel"))
        return _deny_hook(t("confirm_deny_timeout", mins=max(1, _CONFIRM_TIMEOUT_SEC // 60)))

    return _hook

# ── 讀 session 中繼資料 ──────────────────────────────────────────────────
# discord bot 在每則 prompt 前加的 [名字]: 標記，用來辨識「本 bot 自己的對話」，
# 把桌面版 CC、其他專案（如 emoji bot）的 session 濾掉，不混在同一層
_BOT_PROMPT_RE = re.compile(r'^\[.+?\]:\s')

def _session_meta(jf: Path, full: bool = False) -> dict:
    """讀 session jsonl，回傳 {is_bot, title, first_prompt, cwd, has_body}。
    full=False（預設）：非 bot session 讀到第一句就提前結束以省時（給「我的對話」用）。
    full=True：讀完整檔以取得標題（給「電腦上全部」用，非 bot session 標題在後面）。
    has_body：檔內有無真正對話（user/assistant record）；只有 aiTitle 的空殼為 False。"""
    title, first_prompt, cwd, is_bot, has_body = "", "", "", False, False
    try:
        with jf.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"aiTitle"' in line:
                    try:
                        val = json.loads(line).get("aiTitle")   # 不叫 t：避免遮蔽 i18n 的 t()
                        if val:
                            title = val
                    except Exception:
                        pass
                    continue
                if not has_body and ('"type":"user"' in line or '"type":"assistant"' in line):
                    has_body = True   # 出現真正對話內容，非空殼
                if '"type":"user"' in line and not first_prompt:
                    try:
                        o = json.loads(line)
                        cwd = o.get("cwd", "") or cwd
                        content = o.get("message", {}).get("content")
                        if isinstance(content, str):
                            raw = content
                        elif isinstance(content, list):
                            raw = next((b.get("text", "") for b in content
                                        if isinstance(b, dict) and b.get("type") == "text"), "")
                        else:
                            raw = ""
                        raw = raw.strip()
                        is_bot = bool(_BOT_PROMPT_RE.match(raw))
                        first_prompt = _BOT_PROMPT_RE.sub("", raw)  # 去掉 [名字]: 前綴給顯示
                    except Exception:
                        pass
                    if not is_bot and not full:
                        break  # 非 bot session 且非完整模式，不用再往下讀
    except Exception:
        pass
    return {"is_bot": is_bot, "title": title, "first_prompt": first_prompt, "cwd": cwd, "has_body": has_body}

# ── 中文標題快取（讀內容生成、可手動重命名）────────────────────────────────
_titles_cache: dict = {"mtime": None, "data": {}}

def _load_titles() -> dict:
    """讀標題快取檔（帶 mtime 記憶體快取：檔案沒變就不重讀——每則訊息都會查標題）。"""
    try:
        mtime = _TITLES_FILE.stat().st_mtime
    except OSError:
        return {}
    if _titles_cache["mtime"] != mtime:
        try:
            _titles_cache["data"] = json.loads(_TITLES_FILE.read_text(encoding="utf-8"))
            _titles_cache["mtime"] = mtime
        except Exception:
            return {}
    return _titles_cache["data"]

def _save_title(session_id: str, title: str) -> None:
    try:
        data = _load_titles()
        data[session_id] = title
        _TITLES_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _read_session_text(session_id: str, max_chars: int = 3000, keep: str = "head") -> str:
    """讀出 session 的對話文字（使用者+助理）。
    keep="head"：從頭累積到 max_chars（生成標題用，行為不變）。
    keep="both"：讀完整段後取頭尾各半、中間以省略標記接起，
    讓交接稿同時保留「原始目標」與「最近進度」兩端。"""
    claude_home = Path.home() / ".claude" / "projects"
    parts: list[str] = []
    for jf in claude_home.glob(f"*/{session_id}.jsonl"):
        try:
            with jf.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"type":"user"' not in line and '"type":"assistant"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    content = o.get("message", {}).get("content")
                    if isinstance(content, str):
                        txt = content
                    elif isinstance(content, list):
                        txt = " ".join(b.get("text", "") for b in content
                                       if isinstance(b, dict) and b.get("type") == "text")
                    else:
                        txt = ""
                    txt = _BOT_PROMPT_RE.sub("", txt.strip())
                    if txt:
                        parts.append(txt)
                    # head 模式：累積到上限就停；both 模式：讀完整段再裁頭尾
                    if keep == "head" and sum(len(p) for p in parts) > max_chars:
                        break
        except Exception:
            pass
        break
    full = "\n".join(parts)
    if keep == "both" and len(full) > max_chars:
        half = max_chars // 2
        return full[:half] + "\n\n[...]\n\n" + full[-half:]
    return full[:max_chars]

async def _iter_messages(client: "ClaudeSDKClient") -> AsyncIterator:
    """逐一取出 client 的訊息並解析成 SDK 物件；解析失敗的訊息直接跳過。
    集中存取 SDK 私有介面（_query.receive_messages）的唯一入口：公開 API 遇到
    MessageParseError 會中斷整個回合，這裡改為跳過壞訊息，維持長任務的韌性。
    SDK 升版若動到私有介面，只需要修這一個函式。"""
    async for raw in client._query.receive_messages():
        try:
            yield parse_message(raw)
        except MessageParseError:
            continue

def _purge_title_shell(meta_sid: Optional[str]) -> None:
    """清掉 meta 查詢（_ask_haiku）殘留的空殼 session 檔。
    _ask_haiku 已設 no-session-persistence 抑制對話本體，但 CLI 內建 auto-title
    偶爾會搶在行程結束前寫入一行 aiTitle，留下只有標題、無對話本體的空殼（約 105 bytes）。
    這裡只針對「這次 meta 查詢自己的 session id」、且經 _session_meta 確認無對話本體
    （has_body 為 False）才刪；真實對話一定有 user/assistant 本體，絕不會被誤刪。"""
    if not meta_sid:
        return
    claude_home = Path.home() / ".claude" / "projects"
    for jf in claude_home.glob(f"*/{meta_sid}.jsonl"):
        try:
            if not _session_meta(jf)["has_body"]:
                jf.unlink()
        except OSError:
            pass
        break

async def _ask_haiku(prompt: str, model: Optional[str] = "claude-haiku-4-5-20251001") -> str:
    """一次性、不留 session 檔的輕量呼叫。
    預設用 Haiku（生成標題用）；交接稿改傳當前 session 模型以品質優先。
    model=None 時交由 CLI 用帳號預設模型。"""
    opts = ClaudeAgentOptions(
        cwd=str(DEFAULT_DIR),
        cli_path=CLAUDE_CLI,
        model=model,
        permission_mode="bypassPermissions",
        extra_args={"no-session-persistence": None},  # 不寫 session 檔，避免污染
    )
    out = ""
    meta_sid: Optional[str] = None
    async with ClaudeSDKClient(opts) as c:
        await c.query(prompt)
        async for msg in _iter_messages(c):
            if isinstance(msg, ResultMessage):
                out = msg.result or ""
                meta_sid = msg.session_id
                break
    _purge_title_shell(meta_sid)   # no-session-persistence 偶爾仍被 CLI auto-title 搶寫成空殼，用完即焚
    return out.strip()

async def _generate_title(session_id: str) -> Optional[str]:
    """讀 session 內容，用 Haiku 生成一個貼切的短標題（語言跟介面語系走）。"""
    text = _read_session_text(session_id)
    if not text:
        return None
    raw = await _ask_haiku(t("title_prompt") + text)
    title = (raw or "").strip().splitlines()[0] if raw else ""
    title = title.strip('「」"\'*#＊ 　')[:40]   # 去掉引號、markdown 符號、前後空白
    return title or None

async def _generate_handoff(session_id: str, model: Optional[str]) -> Optional[str]:
    """讀目前 session 的頭尾內容，用當前 session 模型生成一份交接稿，
    讓另一台電腦的全新 CC 貼上就能接手（對方讀不到本機檔案，故細節直接寫進稿裡）。"""
    text = _read_session_text(session_id, max_chars=50000, keep="both")
    if not text:
        return None
    raw = await _ask_haiku(t("handoff_prompt") + "\n\n" + text, model=model)
    return (raw or "").strip() or None

def _list_sessions(scope: str = "mine", limit: int = 15) -> list[dict]:
    """掃所有專案列出 session，依時間新到舊。
    scope="mine"：只回本 bot 自己的對話；scope="all"：電腦上所有對話（含桌面版）。"""
    claude_home = Path.home() / ".claude" / "projects"
    if not claude_home.exists():
        return []
    full = (scope == "all")
    titles = _load_titles()
    entries: list[dict] = []
    for proj_dir in claude_home.iterdir():
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            meta = _session_meta(jf, full=full)
            if scope == "mine" and not meta["is_bot"]:
                continue
            if scope == "all" and not meta["has_body"]:
                continue  # all 模式：略過只有標題、無對話本體的空殼 session
            entries.append({
                "session_id": jf.stem,
                "project_path": meta["cwd"] or str(DEFAULT_DIR),
                "mtime": jf.stat().st_mtime,
                # 優先用快取的中文標題（手動或讀內容生成的）
                "title": titles.get(jf.stem) or meta["title"],
                "first_prompt": meta["first_prompt"],
            })
    return sorted(entries, key=lambda e: e["mtime"], reverse=True)[:limit]

def _session_label(session_id: Optional[str]) -> str:
    """由 session_id 找出該對話的標題（優先用快取中文標題），給狀態顯示用。"""
    if not session_id:
        return t("new_chat")
    cached = _load_titles().get(session_id)
    if cached:
        return cached[:60]
    claude_home = Path.home() / ".claude" / "projects"
    for jf in claude_home.glob(f"*/{session_id}.jsonl"):
        meta = _session_meta(jf)
        label = meta["title"] or meta["first_prompt"]
        return label[:60] if label else t("chat_n", id=session_id[:8])
    return t("chat_n", id=session_id[:8])

# ── Claude 執行 ────────────────────────────────────────────────────────
def _fold_messages(messages: list) -> tuple[str, Optional[str], int]:
    """把一回合收到的 SDK 訊息摺疊成 (回覆文字, session_id, ctx_tokens)。
    純函式、無副作用，供單元測試以假訊息流驗證。
    關鍵行為：空 result 不可蓋掉前面文字塊已累積的內容——AskUserQuestion 收尾的
    result 是空的，蓋掉會讓「問題前的說明文字」消失（歷史真 bug，勿回歸）。"""
    content, new_sid, ctx = "", None, 0
    for m in messages:
        if isinstance(m, ResultMessage):
            if m.result:
                content = m.result
            new_sid = m.session_id or new_sid
            usage = getattr(m, "usage", None) or {}
            ctx = (usage.get("input_tokens", 0)
                   + usage.get("cache_read_input_tokens", 0)
                   + usage.get("cache_creation_input_tokens", 0)) or ctx
        elif isinstance(m, AssistantMessage) and not content:
            for block in m.content:
                if hasattr(block, "text"):
                    content += block.text
    return content, new_sid, ctx

def _clean_reply(content: str) -> str:
    """清除回覆中的 [[MILESTONE:...]] 標記與 ThinkingBlock 殘留。純函式。"""
    content = _MILESTONE_RE.sub("", content)
    return re.sub(r'\[ThinkingBlock\(thinking=.*?\)\]', '', content, flags=re.DOTALL).strip()

async def run_claude(
    prompt: str,
    state: ChannelState,
    progress_msg: Optional[discord.Message] = None,
) -> tuple[str, Optional[str], Optional[dict]]:
    """回傳 (content, new_session_id, ask_question_data)"""
    tool_log: list[str] = []
    tool_count: int = 0
    start = time.time()
    pending_question: dict = {}
    live_text: str = ""  # 生成中累積的回應文字（給動畫即時顯示）
    last_activity = [time.time()]  # CC 最後一次有輸出的時間（閒置逾時判斷用）
    # 本回合指令原文（去掉 [名字]: 前綴、壓成單行、去反引號、截斷），顯示在思考訊息頂部供使用者核對，
    # 一眼確認「在跑的是自己給的指令」（防幻象指令、防檔案/網路夾帶的惡意指令）
    _cmd_disp = _BOT_PROMPT_RE.sub("", prompt).replace("\n", " ").replace("`", "'").strip()[:100]

    async def on_stream(upd) -> None:
        nonlocal pending_question, tool_count
        if getattr(upd, "tool_calls", None):
            for tc in upd.tool_calls:
                name = tc.get("name", "?")
                inp = tc.get("input", {})
                if name == "AskUserQuestion":
                    pending_question = inp
                tool_log.append(_fmt_tool(name, inp))
                tool_count += 1
        if getattr(upd, "type","") == "assistant" and getattr(upd, "content", None):
            text = upd.content
            line = text.strip().split("\n",1)[0][:100]
            if line and not line.startswith("["):
                tool_log.append(f"💭 {line}")

    _spinner = ["🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"]

    async def _animate() -> None:
        i = 0
        while True:
            await asyncio.sleep(1.5)
            if not progress_msg:
                continue
            elapsed = int(time.time() - start)
            body = "\n".join(tool_log[-6:])
            # 生成階段：顯示回應字數 + 即時文字尾段，作為「還活著」的存活訊號
            if live_text:
                tail = live_text[-220:].replace("\n", " ")
                body += "\n\n" + t("generating", n=len(live_text)) + f"\n> {tail}"
            icon = _spinner[i % len(_spinner)]
            i += 1
            # 頂部第一行：本回合正在處理的指令原文，讓使用者核對「在跑的是我給的指令」
            _cmd_line = f"📥 `{_cmd_disp}`\n" if _cmd_disp else ""
            # 第二行顯示目前 session + 模型/思考程度，工作時一眼掌握在哪個對話、用什麼設定
            if state._session_label:
                _m = state.model
                _ms = _m.replace("claude-", "") if _m else t("default_inline")
                _eff = state.effort or t("default_inline")
                hdr = _cmd_line + f"💬 `{state._session_label}`　🧠 `{_ms}·{_eff}`\n"
            else:
                hdr = _cmd_line
            try:
                await progress_msg.edit(content=f"{hdr}{icon} **{t('thinking_inline')}** `{elapsed}s`\n{body}"[:1990])
            except Exception:
                pass

    messages = []

    async def _run_client() -> None:
        nonlocal live_text
        try:
            client = await _acquire_client(state)   # 長駐：取池中 client，無則新建連線
            await client.query(prompt)
            async for message in _iter_messages(client):
                last_activity[0] = time.time()  # 有任何訊息=還活著，重置閒置計時
                # 逐字串流：累積生成中的回應文字供動畫顯示
                if isinstance(message, StreamEvent):
                    ev = message.event or {}
                    if ev.get("type") == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":
                            live_text += delta.get("text", "")
                    continue
                messages.append(message)
                if isinstance(message, ResultMessage):
                    _client_used[state._cid] = time.time()  # 標記活躍；client 留池不關
                    break
                if isinstance(message, AssistantMessage):
                    content = getattr(message, "content", [])
                    tc_list, text_parts = [], []
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, ToolUseBlock):
                                tc_list.append({"name": block.name, "input": getattr(block,"input",{})})
                            elif hasattr(block, "text"):
                                text_parts.append(block.text)
                    class _U:
                        def __init__(self,**kw): self.__dict__.update(kw)
                    if tc_list:    await on_stream(_U(type="tool",      tool_calls=tc_list,  content=None))
                    if text_parts: await on_stream(_U(type="assistant", content="\n".join(text_parts), tool_calls=None))
        except Exception:
            # 長駐 client 可能已損壞（連線斷／進程死）→ 丟棄，下次重建並 resume 接回
            await _drop_client(state._cid)
            raise

    anim_task = asyncio.create_task(_animate()) if progress_msg else None
    client_task = asyncio.create_task(_run_client())
    try:
        # 閒置逾時：不限總時長，只在「連續無輸出」超過門檻才視為卡死，
        # 讓長工作流（只要持續有輸出）能像桌面版一樣一直跑下去。
        # 用 asyncio.wait 而非 sleep 輪詢：任務一完成立即返回，回覆不再多等輪詢間隔
        while not client_task.done():
            await asyncio.wait({client_task}, timeout=5)
            if not client_task.done() and time.time() - last_activity[0] > INACTIVITY_TIMEOUT:
                raise asyncio.TimeoutError()
        await client_task  # 完成：取回結果或重新拋出 _run_client 內的例外
    finally:
        if not client_task.done():
            client_task.cancel()
            try:
                await client_task
            except BaseException:
                pass
            # 被取消＝逾時或 /stop 中斷：client 卡在半途，丟棄以免下次接到半截回應
            try:
                await _drop_client(state._cid)
            except BaseException:
                pass
        if anim_task:
            anim_task.cancel()

    content, new_sid, ctx = _fold_messages(messages)
    if ctx:
        state.ctx_tokens = ctx
    reply = _clean_reply(content)
    # Bug A 修復：思考／輸出把 token 額度燒光（stop_reason=max_tokens）時，CC 常一個字都沒
    # 留下。別再靜默當「無回應」吞掉，改回明確提示，讓使用者知道發生什麼、怎麼調整再重試。
    if not reply:
        _stop = None
        for _m in reversed(messages):
            _sr = getattr(_m, "stop_reason", None)
            if _sr:
                _stop = _sr
                break
        if _stop == "max_tokens":
            return t("max_tokens_hint"), new_sid, pending_question or None
    return reply or _NO_RESPONSE, new_sid, pending_question or None

# ── [[FILE:路徑]] 標記並上傳檔案 ─────────────────────────────────────────
_FILE_MARKER = re.compile(r"\[\[FILE:\s*(.+?)\s*\]\]")
_DISCORD_FILE_LIMIT = 25 * 1024 * 1024

# ── 螢幕截圖（手機遠端看電腦畫面）──────────────────────────────────────
def _capture_screenshot_sync() -> Optional[Path]:
    """截取整個虛擬螢幕（含多螢幕）成 PNG，回傳路徑；失敗回 None。"""
    import subprocess
    import tempfile
    out = Path(tempfile.gettempdir()) / f"cc_shot_{uuid.uuid4().hex[:8]}.png"
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$vs=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap $vs.Width,$vs.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($vs.X,$vs.Y,0,0,$bmp.Size);"
        f"$bmp.Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30)
        return out if out.exists() and out.stat().st_size > 0 else None
    except Exception:
        return None

async def _capture_screenshot() -> Optional[Path]:
    return await asyncio.to_thread(_capture_screenshot_sync)

# ── 開車模式語音核心（STT/TTS/GPU 工具）已抽到 drive_core.py（選配、可整包移除）──
# 只留與介面語系綁定的朗讀參考音對應（drive_core 為保持純邏輯，不依賴主程式 i18n）。
def _speak_ref_lang() -> str:
    """朗讀參考音語系：跟著介面語系（zh-TW -> 中文參考音、其餘 -> 英文）。"""
    return "zh" if BOT_LANG == "zh-TW" else "en"

# ── 簡報轉 PDF（簡報是視覺導向，轉 PDF 讓 CC 逐頁視覺讀取更準）────────────
def _pptx_to_pdf_sync(src: Path) -> Optional[Path]:
    """用 PowerPoint COM 把 .ppt/.pptx 轉成同名 .pdf；失敗回傳 None。"""
    import pythoncom
    import win32com.client
    pdf = src.with_suffix(".pdf")
    pythoncom.CoInitialize()
    pp = None
    deck = None
    try:
        pp = win32com.client.Dispatch("PowerPoint.Application")
        # PowerPoint 不允許 Visible=False，改用 WithWindow=False 開啟簡報不顯示視窗
        deck = pp.Presentations.Open(str(src), WithWindow=False)
        deck.SaveAs(str(pdf), 32)  # 32 = ppSaveAsPDF
        return pdf if pdf.exists() else None
    except Exception as e:
        print(f"[PPTX2PDF] 轉檔失敗 {src.name}: {e}", flush=True)
        return None
    finally:
        # Dispatch 會附著到使用者已開啟的 PowerPoint 實例：只關自己開的簡報；
        # 只有整個程式已無任何開啟中的簡報時才 Quit，否則會把使用者開著的工作一起關掉
        try:
            if deck is not None:
                deck.Close()
        except Exception:
            pass
        try:
            if pp is not None and pp.Presentations.Count == 0:
                pp.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()

async def _convert_pptx(src: Path) -> Optional[Path]:
    """非同步轉檔，加 60s timeout；卡住或失敗回傳 None。"""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_pptx_to_pdf_sync, src), timeout=60)
    except Exception:
        return None

async def _maybe_auto_compact(channel: discord.TextChannel, state: ChannelState) -> None:
    """context 達到門檻時自動執行 /compact，避免 CONTEXT_FULL。"""
    if state.ctx_tokens < int(_ctx_limit(state) * 0.85):
        return
    msg = await channel.send(t("compacting"))
    try:
        _, compact_sid, _ = await run_claude(
            t("compact_prompt"),
            state,
        )
        if compact_sid:
            state.session_id = compact_sid
            _persist_session(state)
        state.ctx_tokens = 0
        await msg.edit(content=t("compacted"))
        await asyncio.sleep(1.5)
        await msg.delete()
    except Exception as e:
        await msg.edit(content=t("compact_failed", e=e))

async def _emit_coord(channel, text: str) -> str:
    """解析回覆中的 [[COORD:]] 廣播：更新登錄表、發到協作頻道，回傳去標記後的文字。"""
    items, clean = coord_core.parse_broadcasts(text)
    if not items:
        return text
    cid = getattr(channel, "id", 0)
    name = getattr(channel, "name", None) or str(cid)
    for it in items:
        _coord_registry.update(cid, name, it)
    if COORD_CHANNEL and COORD_CHANNEL != cid:
        dest = bot.get_channel(COORD_CHANNEL)
        if dest is not None:
            for it in items:
                try:
                    await dest.send(t("coord_broadcast", name=name, task=it))
                except Exception:
                    pass
    return clean

async def _send_files_and_text(channel, text: str) -> None:
    if COORD_ENABLED:
        text = await _emit_coord(channel, text)
    paths = _FILE_MARKER.findall(text)
    clean = _FILE_MARKER.sub("", text).strip()

    if clean:
        await send_long(channel, clean)

    for p in paths:
        fp = Path(p.strip().strip('"').strip("'"))
        if not fp.exists() or not fp.is_file():
            await channel.send(t("file_not_found", fp=fp))
            continue
        if fp.stat().st_size > _DISCORD_FILE_LIMIT:
            await channel.send(t("file_too_large", name=fp.name, fp=fp))
            continue
        try:
            await channel.send(file=discord.File(str(fp)))
        except Exception as e:
            await channel.send(t("file_upload_failed", name=fp.name, e=e))

# ── 開車模式語音回覆：把 CC 附的朗讀版抽出來合成語音檔（核心在 drive_core）─────
async def _voice_reply(channel, reply: str, speak: bool) -> str:
    """處理 CC 回覆裡的朗讀版（<<<SPEAK>>>...<<<ENDSPEAK>>>）標記。
    speak=True（開車模式＋語音輸入）：抽出朗讀版、合成語音檔上傳，回傳「去掉標記」的
    純文字（文字版照常完整顯示）。speak=False 或無標記：只把殘留標記清乾淨後回傳。
    任何 TTS 失敗都降級為純文字，不影響文字回覆。drive_core 缺席時原樣返回。"""
    if drive_core is None:
        return reply  # 沒有開車模組時 CC 不會產生朗讀標記，原樣返回
    spoken, clean = drive_core.parse_speak(reply)
    if spoken is None:
        return reply  # 無朗讀標記
    if not speak or not spoken:
        return clean
    try:
        out = Path(__file__).parent / "tmp" / f"speak_{uuid.uuid4().hex[:8]}.wav"
        out.parent.mkdir(exist_ok=True)
        await asyncio.to_thread(drive_core.synthesize, spoken, str(out), _speak_ref_lang())
        await channel.send(file=discord.File(str(out)))
        try:
            out.unlink()
        except Exception:
            pass
    except Exception as e:
        print(f"[F5] 合成失敗，降級純文字：{e}", flush=True)
    return clean

# ── 分段送訊息 ─────────────────────────────────────────────────────────
async def send_long(channel, text: str) -> None:
    # 超長回覆（會被切成 4 則以上）改存成 Markdown 檔 + 簡短預覽，手機上好讀又能存檔
    if len(text) > 3 * MAX_MSG:
        import tempfile
        fp = Path(tempfile.gettempdir()) / f"cc_reply_{uuid.uuid4().hex[:8]}.md"
        try:
            fp.write_text(text, encoding="utf-8")
            preview = text[:1500].rstrip()
            await channel.send(preview + t("reply_long_preview"),
                               file=discord.File(str(fp)))
        finally:
            try:
                fp.unlink()
            except Exception:
                pass
        return
    for i in range(0, len(text), MAX_MSG):
        await channel.send(text[i:i+MAX_MSG])
        if i + MAX_MSG < len(text):
            await asyncio.sleep(0.3)

# ── AskUserQuestion → Discord 按鈕 ─────────────────────────────────────
def _parse_ask_questions(ask_data: dict) -> list[dict]:
    if "questions" in ask_data:
        return ask_data["questions"]
    return [ask_data]

async def _handle_cc_error(prog: discord.Message, err: "CCError", state: ChannelState) -> None:
    msg = err.user_msg
    if err.kind in _RESET_SESSION:
        state.session_id = None
        state.ctx_tokens = 0
        _persist_session(state)
        await _drop_client(state._cid)   # session 已失效，關掉長駐 client（A'）
        msg += t("session_auto_cleared")
    if err.kind == "OVERLOADED":
        inc = await asyncio.to_thread(is_incident_active)
        if inc:
            msg += t("official_status", inc=inc)
    raw = (err.raw or "").strip()
    if raw:
        msg += f"\n```\n[{err.kind}] {raw[:1500]}\n```"
    try:
        await prog.edit(content=msg[:2000])
    except Exception:
        pass

async def _process_answer(channel: discord.TextChannel, chosen: str, state: ChannelState) -> None:
    if _processing.get(channel.id):
        await channel.send(t("still_processing"), delete_after=5)
        return
    _processing[channel.id] = True
    prog = await channel.send(t("you_chose_thinking", chosen=chosen))
    try:
        reply, new_sid, next_ask = await _run_tracked(channel.id, chosen, state, prog)
        if new_sid:
            state.session_id = new_sid
            _persist_session(state)
        await prog.delete()
        # 按鈕回答並非語音輸入，不合成語音；但仍過 _voice_reply 清掉可能殘留的朗讀標記
        if next_ask:
            # 先送出說明文字（思考結果），再送下一題問題按鈕
            if reply and reply != _NO_RESPONSE:
                await _send_files_and_text(channel, await _voice_reply(channel, reply, speak=False))
            await _send_ask_question(channel, next_ask, state)
        elif reply and reply != _NO_RESPONSE:
            await _send_files_and_text(channel, await _voice_reply(channel, reply, speak=False))
    except _StoppedByUser:
        await prog.edit(content=t("stopped"))
    except CCError as e:
        await _handle_cc_error(prog, e, state)
    except Exception as e:
        print(f"[CC_FAIL] kind=UNCLASSIFIED raw={e!r}", flush=True)
        detail = f"{type(e).__name__}: {e}"[:1500]
        await prog.edit(content=t("unexpected_error", detail=detail)[:2000])
    finally:
        _processing[channel.id] = False

async def _send_ask_question(channel: discord.TextChannel, ask_data: dict, state: ChannelState) -> None:
    # Discord 一問一答架構一次只能收一題答案，多題會被吞掉；故一次只處理第一題（防呆）
    questions = _parse_ask_questions(ask_data)[:1]
    for q in questions:
        question_text = q.get("question", t("choose_prompt"))
        raw_options: list = q.get("options", [])

        def _label(o) -> str:
            if isinstance(o, dict):
                return o.get("label") or o.get("value") or str(o)
            return str(o)

        def _desc(o) -> str:
            return o.get("description", "") if isinstance(o, dict) else ""

        opt_labels = [_label(o) for o in raw_options]
        opt_descs = [_desc(o) for o in raw_options]

        if not opt_labels:
            await channel.send(t("type_to_reply", q=question_text))
            continue

        # 每個選項顯示「粗體標題＋下一行 blockquote 說明」，讓使用者看得到 description
        _lines = []
        for i, l in enumerate(opt_labels):
            d = opt_descs[i].strip()[:200]   # 過長截斷，避免超過 Discord 2000 字上限
            _lines.append(f"`{i+1}.` **{l}**" + (f"\n> {d}" if d else ""))
        numbered = "\n".join(_lines)
        body = f"❓ **{question_text}**\n{numbered}\n" + t("ask_hint")

        state.pending_options = opt_labels

        view = discord.ui.View(timeout=600)
        answered = {"done": False}
        sent: dict = {"msg": None}

        async def on_to(v: discord.ui.View = view) -> None:
            # 逾時：把按鈕真的置灰——要 edit 訊息才會反映到 Discord 畫面，
            # 只改記憶體的話使用者仍看到可點的按鈕、點了卻顯示互動失敗。
            # 打數字作答的路徑不受影響，逾時後仍可用文字回答。
            for item in v.children:
                item.disabled = True
            answered["done"] = True
            m = sent.get("msg")
            if m is not None:
                try:
                    await m.edit(view=v)
                except Exception:
                    pass

        view.on_timeout = on_to

        async def _finish(inter: discord.Interaction, chosen: str) -> None:
            if answered["done"]:
                try:
                    await inter.response.send_message(t("question_ended"), ephemeral=True)
                except Exception:
                    pass
                return
            answered["done"] = True
            state.pending_options = None
            for item in view.children:
                item.disabled = True
            view.stop()
            try:
                await inter.response.edit_message(content=f"{body}\n\n" + t("selected", chosen=chosen), view=view)
            except Exception:
                try:
                    await inter.response.defer()
                except Exception:
                    pass
            await _process_answer(channel, chosen, state)

        try:
            if len(opt_labels) <= 5:
                for label in opt_labels:
                    btn = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.primary)
                    async def cb(inter: discord.Interaction, l: str = label) -> None:
                        await _finish(inter, l)
                    btn.callback = cb
                    view.add_item(btn)
            else:
                opts = [discord.SelectOption(label=l[:100], value=str(i),
                                             description=(opt_descs[i][:100] or None))
                        for i, l in enumerate(opt_labels)]
                sel = discord.ui.Select(placeholder=t("select_placeholder"), options=opts[:25])
                async def sel_cb(inter: discord.Interaction) -> None:
                    idx = int(sel.values[0])
                    await _finish(inter, opt_labels[idx])
                sel.callback = sel_cb
                view.add_item(sel)
            sent["msg"] = await channel.send(body, view=view)
        except Exception:
            await channel.send(body)

# ── Usage API ──────────────────────────────────────────────────────────
def fetch_usage() -> Optional[dict]:
    now = time.time()
    if "data" in _usage_cache and now - _usage_cache.get("ts",0) < USAGE_CACHE_SEC:
        return _usage_cache["data"]
    try:
        creds = json.loads((Path.home()/".claude"/".credentials.json").read_text())
        token = creds["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={"Authorization":f"Bearer {token}","anthropic-beta":"oauth-2025-04-20",
                     "User-Agent":"claude-code/2.1.143","Content-Type":"application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        _usage_cache["data"] = data
        _usage_cache["ts"] = now
        return data
    except Exception:
        return None

def _bar(pct: float, w: int = 14) -> str:
    f = max(0, min(w, round(pct/100*w)))   # 夾在 0..w：用量超過上限時避免負數 padding 讓進度條變形
    return "█"*f + "░"*(w-f)

def _reset(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone().strftime("%H:%M")
    except Exception:
        return ts

def _countdown(ts: str) -> str:
    try:
        target = datetime.fromisoformat(ts.replace("Z","+00:00"))
        delta = target - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return t("reset_soon")
        h, m = secs // 3600, (secs % 3600) // 60
        if h >= 24:
            d = h // 24
            return t("in_days", d=d, h=h % 24)
        if h:
            return t("in_hours", h=h, m=m)
        return t("in_mins", m=m)
    except Exception:
        return ts

# ── 排程背景循環 ───────────────────────────────────────────────────────
async def _client_reaper() -> None:
    """背景迴圈：定期回收閒置逾時的長駐 client，釋放記憶體；下次訊息再以 resume 接回。"""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for cid in list(_clients):
            if _processing.get(cid):          # 正在處理中的不回收
                continue
            if now - _client_used.get(cid, 0) > CLIENT_IDLE_TIMEOUT:
                await _drop_client(cid)


async def _schedule_loop() -> None:
    """每 30 秒檢查到期排程並執行。"""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(30)
        try:
            schedules = _load_schedules()
            now = datetime.now()
            modified = False
            for s in schedules:
                try:
                    next_run = datetime.fromisoformat(s["next_run"])
                    # CC 解析偶爾會給帶時區的 ISO 字串；與 naive 的 now 比較會 TypeError，
                    # 一路炸到外層 except 讓整輪排程停擺，故先轉本地時間再去掉時區
                    if next_run.tzinfo is not None:
                        next_run = next_run.astimezone().replace(tzinfo=None)
                except Exception:
                    continue
                if now < next_run:
                    continue
                # 到期 → 執行。頻道正在處理訊息時整輪跳過（不更新 next_run、30 秒後再試），
                # 執行期間也佔住 _processing——避免兩個 run_claude 同時打進同一個長駐
                # client 造成串流交錯，也讓使用者訊息收到「處理中」提示而非默默排隊
                channel = bot.get_channel(s["channel_id"])
                if channel:
                    cid = s["channel_id"]
                    if _processing.get(cid):
                        continue
                    _processing[cid] = True
                    state = get_state(cid)
                    await channel.send(t("run_schedule", task=s['task']))
                    try:
                        reply, new_sid, _ = await run_claude(s["task"], state)
                        if new_sid:
                            state.session_id = new_sid
                            _persist_session(state)
                        if reply and reply != _NO_RESPONSE:
                            await send_long(channel, reply)
                    except Exception as e:
                        await channel.send(t("schedule_run_failed", e=e))
                    finally:
                        _processing[cid] = False
                # 計算下次執行時間
                cron_expr = s.get("cron", "").strip()
                if _HAS_CRONITER and cron_expr:
                    try:
                        s["next_run"] = _croniter(cron_expr, now).get_next(datetime).isoformat()
                        modified = True
                    except Exception:
                        s["_delete"] = True
                        modified = True
                else:
                    # 一次性任務
                    s["_delete"] = True
                    modified = True
            if modified:
                schedules = [s for s in schedules if not s.get("_delete")]
                _save_schedules(schedules)
        except Exception as e:
            print(f"[SCHEDULE_LOOP] error: {e}", flush=True)

# ── Bot 本體 ───────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

async def _update_presence(cid: int, label: str) -> None:
    """把「頻道名｜session 標題」設成 bot 的 Discord 狀態。
    多頻道時 Discord 只能有一個全域狀態，加頻道名讓你看得出顯示的是哪個頻道。"""
    ch = bot.get_channel(cid)
    name = getattr(ch, "name", None) or "cc"
    try:
        await bot.change_presence(activity=discord.CustomActivity(name=f"💬 {name}｜{label}"[:128]))
    except Exception:
        pass

# ── 多 session 側欄（頻道即對話）─────────────────────────────────────────
_sidebar_category_id: Optional[int] = None   # 「CC 對話」分類 id
_sidebar_entry_id: Optional[int] = None      # 入口頻道 id

def _safe_channel_name(title: str) -> str:
    """把標題轉成可用的頻道名：去頭尾空白、換行轉空白、截斷到 90 字。"""
    name = " ".join((title or "").split())[:90].strip()
    return name or t("untitled_chat")

WT_PREFIX = "🌿"   # worktree 頻道在側欄的視覺前綴（左側列表一眼分辨平行分支）

def _strip_wt_prefix(name: str) -> str:
    """去掉頻道名開頭的 🌿 前綴（含其後空白或連字號），沒有就原樣回傳。"""
    return re.sub(rf"^{WT_PREFIX}[\s\-]*", "", name or "").strip()

def _channel_display_name(base_title: str, wt_on: bool) -> str:
    """依 worktree 狀態組頻道名：開著加 🌿 前綴，否則就是安全標題本身。"""
    safe = _safe_channel_name(_strip_wt_prefix(base_title))
    return f"{WT_PREFIX} {safe}" if wt_on else safe

async def _rename_for_wt(channel: discord.TextChannel, wt_on: bool) -> None:
    """側欄頻道：依 worktree 狀態加／去 🌿 前綴；改名失敗不影響主流程（純視覺）。"""
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        cur = getattr(channel, "name", "") or ""
        new = _channel_display_name(cur, wt_on)
        if new != cur:
            await channel.edit(name=new)
    except Exception as e:
        print(f"[WT] 側欄前綴更新失敗：{e}", flush=True)

async def _open_sidebar_channel(guild: discord.Guild, category: discord.CategoryChannel,
                                session_id: Optional[str] = None, title: Optional[str] = None,
                                cwd: Optional[str] = None) -> Optional[discord.TextChannel]:
    """在側欄分類頂端（入口下方）建一個頻道並綁 session；有 title 就直接命名、不再自動改名。
    回傳 channel；缺權限或失敗回 None。"""
    name = _safe_channel_name(title) if title else t("new_chat_channel")
    try:
        ch = await guild.create_text_channel(name, category=category, position=1)
    except (discord.Forbidden, Exception) as e:
        print(f"[SIDEBAR] 建頻道失敗：{e}", flush=True)
        return None
    st = get_state(ch.id)
    st.session_id = session_id
    st.cwd = Path(cwd) if cwd and Path(cwd).is_dir() else DEFAULT_DIR
    st._sidebar = True
    st._named = bool(title)   # 救回已有標題的不用再自動改名；全新對話留待第一句後改名
    if title:
        st._session_label = title
        if session_id:
            _save_title(session_id, title)   # 標題寫進快取，/sessions 顯示一致
    _allowed_channels.add(ch.id)
    if session_id:
        _persist_session(st)
    # position=1 是 Discord 全域絕對序，多頻道時不保證落在入口正下方；
    # 故建完再用可靠的相對移動，把新頻道頂到入口下方第一個
    await _bump_channel_to_top(ch)
    return ch

async def _restore_session_to_channel(inter: discord.Interaction, entry: dict) -> None:
    """把選到的歷史對話救回成側欄一個新頻道（保住一頻道一 session）。
    沒有側欄分類時退回舊行為：切換目前頻道。"""
    title = (entry.get("title") or entry.get("first_prompt") or entry.get("snippet") or t("restored_default"))[:60]
    cwd, ok = _resolve_project_dir(entry["project_path"])
    category = bot.get_channel(_sidebar_category_id) if _sidebar_category_id else None
    if inter.guild and isinstance(category, discord.CategoryChannel):
        # 任一分類的既有頻道若已綁著這個 session，直接導航過去，不再重複開一個。
        # 掃持久化的 session map（頻道 id→session_id，跨所有分類；不受 _allowed_channels
        # 重啟後只補回「CC 對話」分類所限），命中且頻道還在就導向它。
        sid = entry["session_id"]
        if sid:
            for _cid_s, _rec in _load_sessions_map().items():
                _rsid = _rec if isinstance(_rec, str) else (_rec or {}).get("session_id")
                if _rsid == sid and _cid_s.isdigit():
                    existing = bot.get_channel(int(_cid_s))
                    if existing is not None:
                        await inter.response.edit_message(
                            content=t("already_open", mention=existing.mention), view=None)
                        return
        ch = await _open_sidebar_channel(inter.guild, category,
                                         session_id=entry["session_id"], title=title, cwd=str(cwd))
        if ch:
            await inter.response.edit_message(
                content=t("restored_to_channel", mention=ch.mention), view=None)
            return
    # 退回舊行為：切換目前頻道
    state = get_state(inter.channel_id)
    state.session_id = entry["session_id"]
    state.cwd = cwd
    state._session_label = title
    _persist_session(state)
    await _drop_client(inter.channel_id)   # 換到別段歷史對話，舊 client 須丟棄重接（A'）
    await _update_presence(inter.channel_id, title)
    note = "" if ok else t("folder_missing", cwd=cwd)
    await inter.response.edit_message(
        content=t("switched_to", title=title, cwd=cwd, note=note), view=None)

class NewChatView(discord.ui.View):
    """入口頻道的常駐「開新對話」按鈕（custom_id 固定、timeout=None，重啟後仍可點）。"""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label=t("btn_new_chat"), style=discord.ButtonStyle.primary, custom_id="cc_new_chat")
    async def new_chat(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # 開新對話：改為所有「授權使用者」皆可按（原本限擁有者）。非授權者才擋下。
        if interaction.user.id not in _allowed_users:
            await interaction.response.send_message(t("no_permission"), ephemeral=True)
            return
        guild = interaction.guild
        category = interaction.channel.category if interaction.channel else None
        if guild is None or category is None:
            await interaction.response.send_message(t("no_category"), ephemeral=True)
            return
        ch = await _open_sidebar_channel(guild, category)
        if ch is None:
            await interaction.response.send_message(
                t("open_channel_failed"), ephemeral=True)
            return
        await interaction.response.send_message(
            t("new_chat_ready", mention=ch.mention), ephemeral=True)

async def _bump_channel_to_top(channel: discord.TextChannel) -> None:
    """把側欄頻道移到入口下方（最新活動置頂）；已在頂端就不動，省 rate limit。"""
    try:
        category = channel.category
        if category is None or category.id != _sidebar_category_id:
            return
        entry = bot.get_channel(_sidebar_entry_id) if _sidebar_entry_id else None
        if entry is None:
            return
        # 已經是入口正下方第一個就不搬
        convo = [c for c in category.text_channels if c.id != _sidebar_entry_id]
        if convo and convo[0].id == channel.id:
            return
        await channel.move(after=entry, category=category)
    except Exception as e:
        print(f"[SIDEBAR] 置頂失敗：{e}", flush=True)

async def _autoname_channel(channel: discord.TextChannel, state: ChannelState) -> None:
    """側欄頻道第一句後：讀內容生成中文標題、改頻道名（受 2 次/10 分鐘改名限制，故只改一次）。"""
    try:
        title = await _generate_title(state.session_id)
        if not title:
            return
        _save_title(state.session_id, title)
        state._session_label = title
        await channel.edit(name=_channel_display_name(title, bool(state.wt)))
        await _update_presence(channel.id, title)
    except Exception as e:
        print(f"[SIDEBAR] 自動改名失敗：{e}", flush=True)

async def _ensure_sidebar(guild: discord.Guild) -> None:
    """確保「CC 對話」分類與入口頻道存在、掛上常駐按鈕，並把分類底下頻道納入可用清單。"""
    global _sidebar_category_id, _sidebar_entry_id
    try:
        category = discord.utils.get(guild.categories, name=SIDEBAR_CATEGORY)
        if category is None:
            category = await guild.create_category(SIDEBAR_CATEGORY)
        _sidebar_category_id = category.id
        # 分類底下的對話頻道全部納入可用清單（入口頻道除外）
        entry = None
        for ch in category.text_channels:
            if ch.name == SIDEBAR_ENTRY or ch.name.startswith("➕"):
                entry = ch
            else:
                _allowed_channels.add(ch.id)
                # 旗標只存在記憶體、不寫檔，bot 重啟就消失；在此替既有側欄頻道補回，
                # 讓重啟前就存在的舊頻道也能繼續自動置頂
                st = get_state(ch.id)
                st._sidebar = True
                st._named = True   # 既有頻道已有名字，標記為已命名以免重啟後被自動改名
        # 使用者可能把對話頻道搬到別的分類自行歸類；那些頻道不在上面的「CC 對話」
        # 分類裡，重啟後不會被上面的迴圈補回 _allowed_channels，會被訊息閘門判為
        # 非授權而不回應。這裡用持久化的 session map（跨分類的頻道↔session 綁定）把
        # 仍存在於本 guild 的頻道補回，只恢復「可回應」，不設 _sidebar（不動使用者
        # 已歸好的分類與位置，也不自動置頂／改名）。
        for _cid_s in _load_sessions_map():
            if not _cid_s.isdigit():
                continue
            _cid = int(_cid_s)
            if _cid not in _allowed_channels and isinstance(
                    guild.get_channel(_cid), discord.TextChannel):
                _allowed_channels.add(_cid)
        # 入口頻道不存在就建，並固定在最上面
        if entry is None:
            entry = await guild.create_text_channel(SIDEBAR_ENTRY, category=category, position=0)
        _sidebar_entry_id = entry.id
        # 入口頻道若還沒有按鈕訊息就發一則（已發過就不重複）
        has_btn = False
        async for m in entry.history(limit=20):
            if m.author == bot.user and m.components:
                has_btn = True
                break
        if not has_btn:
            await entry.send(
                t("entry_message"),
                view=NewChatView())
        print(f"[SIDEBAR] category={_sidebar_category_id} entry={_sidebar_entry_id}", flush=True)
    except discord.Forbidden:
        print("[SIDEBAR] 缺少管理頻道權限，略過側欄初始化", flush=True)
    except Exception as e:
        print(f"[SIDEBAR] 初始化失敗：{e}", flush=True)

async def check_auth(interaction: discord.Interaction, owner_only: bool = False) -> bool:
    # 入口頻道雖不處理一般訊息，但允許在那邊用指令（/sessions、/search 等）
    if interaction.channel_id not in _allowed_channels and interaction.channel_id != _sidebar_entry_id:
        await interaction.response.send_message(t("no_permission"), ephemeral=True)
        return False
    if owner_only and interaction.user.id != ALLOWED_USER:
        await interaction.response.send_message(t("owner_only"), ephemeral=True)
        return False
    if interaction.user.id not in _allowed_users:
        await interaction.response.send_message(t("no_permission"), ephemeral=True)
        return False
    return True

def _sweep_tmp(max_age_h: int = 24) -> None:
    """清掃 tmp/ 內超過 max_age_h 小時的舊檔（收進來的附件、語音等），避免長期堆積。"""
    tmp_dir = Path(__file__).parent / "tmp"
    if not tmp_dir.is_dir():
        return
    cutoff = time.time() - max_age_h * 3600
    for f in tmp_dir.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception:
            pass

_ready_once = False   # on_ready 防重入：斷線後 resume 失敗會重新 identify、再次觸發 on_ready

@bot.event
async def on_ready() -> None:
    # 重連再次觸發時直接略過，否則 _schedule_loop／_client_reaper 會疊加多份
    global _ready_once
    if _ready_once:
        print("[READY] 重連觸發 on_ready，略過重複初始化", flush=True)
        return
    _ready_once = True
    await bot.tree.sync()
    print(t("ready_log", user=bot.user), flush=True)
    asyncio.create_task(asyncio.to_thread(_sweep_tmp))   # 啟動時清掃 tmp/ 舊檔
    asyncio.create_task(_schedule_loop())
    asyncio.create_task(_client_reaper())   # 長駐 client 閒置回收（A'）
    # 開車模式：上次關機時若為開啟，重啟自動恢復載入兩個模型（開車中崩潰可自癒）；
    # 在家（預設 off）則完全不載入語音模型，不吃 GPU/VRAM。
    if _drive_mode and drive_core:
        asyncio.create_task(asyncio.to_thread(drive_core.get_whisper))
        async def _bg_f5() -> None:
            try:
                await asyncio.to_thread(drive_core.get_f5tts)
            except Exception as e:
                print(f"[F5] 預載失敗：{e}", flush=True)
        asyncio.create_task(_bg_f5())
    # 多 session 側欄：註冊常駐按鈕 + 確保分類/入口頻道存在
    bot.add_view(NewChatView())   # 讓重啟前發出的按鈕仍可點
    guild = bot.guilds[0] if bot.guilds else None
    if guild:
        await _ensure_sidebar(guild)

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    """側欄頻道被刪 → 清掉它的 session 記錄與 state（Claude JSONL 留在硬碟，日後可救回）。"""
    if channel.id == _sidebar_entry_id:
        return
    # 頻道若有開 worktree → 嘗試清掉（不加 --force，髒的會被擋下而保留，不丟工作）
    wt_rec = None
    st = _sessions.get(channel.id)
    if st:
        wt_rec = st.wt
    else:
        rec = _load_sessions_map().get(str(channel.id))
        if isinstance(rec, dict):
            wt_rec = rec.get("wt")
    if isinstance(wt_rec, dict) and wt_rec.get("repo") and wt_rec.get("path"):
        try:
            await asyncio.to_thread(wt_core.remove, wt_rec["repo"], wt_rec["path"])
        except Exception:
            pass
    _allowed_channels.discard(channel.id)
    _sessions.pop(channel.id, None)
    await _drop_client(channel.id)   # 關閉並移除該頻道的長駐 client（A'）
    try:
        data = _load_sessions_map()
        if str(channel.id) in data:
            del data[str(channel.id)]
            _SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    """手動在「CC 對話」分類建頻道 → 自動綁一個空 session（bot 自己建的已在 _allowed_channels，會被跳過）。"""
    if not isinstance(channel, discord.TextChannel):
        return
    if (channel.category_id != _sidebar_category_id
            or channel.id == _sidebar_entry_id
            or channel.id in _allowed_channels):
        return
    st = get_state(channel.id)
    st._sidebar = True
    st._named = True   # 手動建的由使用者自己命名，不自動改名
    _allowed_channels.add(channel.id)

# ── Slash 指令 ─────────────────────────────────────────────────────────

@bot.tree.command(name="rename", description=t("cmd_rename_desc"))
async def cmd_rename(interaction: discord.Interaction, name: Optional[str] = None) -> None:
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    sid = state.session_id
    if not sid:
        await interaction.response.send_message(t("rename_no_session"), ephemeral=True)
        return
    await interaction.response.defer()
    if name and name.strip():
        title = name.strip()[:30]
    else:
        title = await _generate_title(sid)
        if not title:
            await interaction.followup.send(t("rename_gen_failed"))
            return
    _save_title(sid, title)
    state._session_label = title
    # 真正改到 Discord 頻道名稱（先前只改了內部標題與 presence，漏了這行）
    try:
        await interaction.channel.edit(name=_channel_display_name(title, bool(state.wt)))
    except Exception as e:
        print(f"[RENAME] 改頻道名失敗：{e}", flush=True)
    await _update_presence(interaction.channel_id, title)
    await interaction.followup.send(t("renamed", title=title))

@bot.tree.command(name="stop", description=t("cmd_stop_desc"))
async def cmd_stop(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    task = _running_tasks.get(interaction.channel_id)
    if task and not task.done():
        task.cancel()
        await interaction.response.send_message(t("stop_sent"))
    else:
        await interaction.response.send_message(t("stop_nothing"), ephemeral=True)

@bot.tree.command(name="continue", description=t("cmd_continue_desc"))
async def cmd_continue(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    if state.session_id:
        await interaction.response.send_message(t("continue_resume", id=state.session_id[:8]))
    else:
        await interaction.response.send_message(t("continue_none"))

@bot.tree.command(name="status", description=t("cmd_status_desc"))
async def cmd_status(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    sid = state.session_id
    label = await asyncio.to_thread(_session_label, sid)
    ctx = state.ctx_tokens
    ctx_limit = _ctx_limit(state)
    ctx_bar = _bar(ctx / ctx_limit * 100)
    lines = [
        t("status_title"),
        t("status_convo", label=label),
        t("status_dir", cwd=state.cwd),
    ]
    if state.wt:
        lines.append(t("status_worktree", branch=state.wt["branch"], base=state.wt["base"]))
    lines += [
        t("status_session", id=sid[:8]) if sid else t("status_session_none"),
        t("status_model", model=state.model or t("default_inline"), fb=FALLBACK_MODEL),
        t("status_effort", effort=state.effort or t("default_inline")),
        t("status_context", bar=ctx_bar, ctx=f"{ctx:,}", limit=f"{ctx_limit:,}"),
    ]
    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="sessions", description=t("cmd_sessions_desc"))
@discord.app_commands.choices(scope=[
    discord.app_commands.Choice(name=t("scope_mine"), value="mine"),
    discord.app_commands.Choice(name=t("scope_all"), value="all"),
])
async def cmd_sessions(interaction: discord.Interaction, scope: str = "mine") -> None:
    if not await check_auth(interaction): return
    await interaction.response.defer(ephemeral=True)   # ephemeral：只有你看得到，入口頻道零殘留
    entries = await asyncio.to_thread(_list_sessions, scope)
    if not entries:
        await interaction.followup.send(t("no_sessions"), ephemeral=True)
        return
    options = []
    for i, e in enumerate(entries):
        dt = datetime.fromtimestamp(e["mtime"]).strftime("%m/%d %H:%M")
        label = (e["title"] or e["first_prompt"] or t("untitled"))[:95]
        # 描述放第一句訊息片段，讓同標題的對話也分得出來
        desc = f"{dt} · {e['first_prompt']}" if e["first_prompt"] else dt
        options.append(discord.SelectOption(label=label, description=desc[:95], value=str(i)))

    class SessionSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder=t("pick_restore"), options=options)
        async def callback(self, inter: discord.Interaction) -> None:
            # entries 綁在本 view 的 closure：多個選單同開也不會互相汙染
            await _restore_session_to_channel(inter, entries[int(self.values[0])])

    view = discord.ui.View()
    view.add_item(SessionSelect())
    header = t("sessions_header_all") if scope == "all" else t("sessions_header_mine")
    await interaction.followup.send(header, view=view, ephemeral=True)

# ── /search 語意搜尋（選配：需 fastembed）────────────────────────────────
# 裝了 fastembed 就用 embedding 向量做語意搜尋（依「描述內容」找，比關鍵字精準）；
# 沒裝就自動退回下方 _search_sessions 的字面關鍵字比對，bot 不會因缺套件崩潰。
_EMBED_MODEL = None           # 延遲載入的 embedding 模型單例
_EMBED_UNAVAILABLE = False    # 標記 fastembed 不可用，避免每次搜尋重複嘗試載入
# e5 是「非對稱」檢索模型：文件要加 "passage: "、查詢要加 "query: " 前綴（訓練時就這樣，
# 不加前綴會明顯掉分）。這正是原註解裡想走、比 paraphrase 系列更適合檢索的方向。
_E5_MODEL_NAME  = "intfloat/multilingual-e5-large"
_INDEX_CHARS    = 6000   # 每個 session 取前 N 字進索引（成本考量，先不吃整段）
_CHUNK_SIZE     = 450    # 單塊字元數：壓在 e5 的 512 token 上限內，避免被靜默截斷
_CHUNK_OVERLAP  = 80     # 相鄰塊重疊字元數，避免語意剛好被切斷在邊界

def _get_embed_model() -> Optional["TextEmbedding"]:  # noqa: F821（fastembed 為選配，型別不在頂層 import）
    """延遲載入 multilingual-e5-large（用到才載入，常駐 RAM 接近零；首次會下載模型）。
    無 fastembed 或載入失敗時回 None，呼叫端據此退回字面搜尋。"""
    global _EMBED_MODEL, _EMBED_UNAVAILABLE
    if _EMBED_UNAVAILABLE:
        return None
    if _EMBED_MODEL is not None:
        return _EMBED_MODEL
    try:
        from fastembed import TextEmbedding
        # multilingual-e5-large：1024 維、100+ 語言含中文、onnxruntime 後端（不需 PyTorch），檢索專用
        _EMBED_MODEL = TextEmbedding(model_name=_E5_MODEL_NAME)
        return _EMBED_MODEL
    except Exception as e:
        _EMBED_UNAVAILABLE = True
        print(f"[search] fastembed 不可用，/search 退回字面搜尋：{e}")
        return None

def _chunk_text(text: str) -> list[str]:
    """把一段文字切成固定字元數、帶重疊的塊；短於一塊就回單塊、空字串回空清單。
    切塊是為了讓每顆向量只承載一小段語意，避免整段對話壓成一坨糊掉，且能搜到後半段。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= _CHUNK_SIZE:
        return [text]
    chunks: list[str] = []
    step = _CHUNK_SIZE - _CHUNK_OVERLAP
    for i in range(0, len(text), step):
        chunk = text[i:i + _CHUNK_SIZE]
        if chunk.strip():
            chunks.append(chunk)
        if i + _CHUNK_SIZE >= len(text):
            break
    return chunks

def _load_vectors() -> dict:
    """讀向量快取檔；不存在或損毀時回空 dict。"""
    try:
        return json.loads(_VECTORS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_vectors(cache: dict) -> None:
    """寫回向量快取檔。"""
    try:
        _VECTORS_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _build_or_update_vectors() -> dict:
    """增量建立/更新所有 bot session 的向量快取，回傳 {session_id: {"mtime", "vec"}}。
    只對新出現或 mtime 變動的 session 重算 embedding（lazy 觸發、無背景常駐）；
    無 fastembed 時回空 dict。"""
    model = _get_embed_model()
    if model is None:
        return {}
    cache = _load_vectors()
    claude_home = Path.home() / ".claude" / "projects"
    if not claude_home.exists():
        return cache
    # 掃出目前所有 bot session 與其 mtime
    current: dict[str, float] = {}
    for proj_dir in claude_home.iterdir():
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            meta = _session_meta(jf)
            if not meta["is_bot"]:
                continue
            current[jf.stem] = jf.stat().st_mtime
    # 挑出需要重算的（新的、mtime 變過的、或還是舊單向量格式的），並先把每段切好塊
    pending: list[tuple[str, float, list[str]]] = []
    for sid, mtime in current.items():
        cached = cache.get(sid)
        if cached and "vecs" in cached and abs(cached.get("mtime", 0.0) - mtime) < 1e-6:
            continue
        chunks = _chunk_text(_read_session_text(sid, max_chars=_INDEX_CHARS))
        if chunks:
            pending.append((sid, mtime, chunks))
    # 把所有待算 session 的塊攤平成一批做 embedding（文件加 passage: 前綴），再依區間切回各 session
    if pending:
        flat: list[str] = []
        spans: list[tuple[str, float, int, int]] = []  # sid, mtime, 起始索引, 結束索引
        for sid, mtime, chunks in pending:
            start = len(flat)
            flat.extend(f"passage: {c}" for c in chunks)
            spans.append((sid, mtime, start, len(flat)))
        vecs = list(model.embed(flat))
        for sid, mtime, start, end in spans:
            cache[sid] = {"mtime": mtime,
                          "vecs": [[float(x) for x in v] for v in vecs[start:end]]}
    # 清掉已不存在的 session，避免快取無限膨脹
    removed = [sid for sid in cache if sid not in current]
    for sid in removed:
        del cache[sid]
    if pending or removed:
        _save_vectors(cache)
    return cache

def _semantic_search(keyword: str, limit: int = 15) -> Optional[list[dict]]:
    """Hybrid 檢索：向量臂（多塊、每 session 取最相似的塊當分數）＋ 字面關鍵字臂，
    以 RRF（Reciprocal Rank Fusion）融合兩邊名次——語意抓相關、關鍵字補精準（人名、
    指令名、錯誤碼這種向量常漏的）。回傳結構與 _search_sessions 一致供 cmd_search 共用；
    無 fastembed/numpy 時回 None，呼叫端據此退回純字面搜尋。"""
    model = _get_embed_model()
    if model is None:
        return None
    try:
        import numpy as np
    except Exception:
        return None
    cache = _build_or_update_vectors()
    if not cache:
        return None
    # 向量臂：查詢加 query: 前綴；所有塊疊成一個矩陣一次算 cosine（免逐塊 Python 迴圈），
    # 再依 session 的列區間取「最相似的那一塊」的分數代表該 session
    qvec = list(model.embed([f"query: {keyword}"]))[0]
    q = np.asarray(qvec, dtype=np.float32)
    q = q / (float(np.linalg.norm(q)) + 1e-9)
    flat: list[list[float]] = []
    spans: list[tuple[str, int, int]] = []   # sid, 起始列, 結束列
    for sid, rec in cache.items():
        vecs = rec.get("vecs") or []
        if vecs:
            spans.append((sid, len(flat), len(flat) + len(vecs)))
            flat.extend(vecs)
    vscored: list[tuple[float, str]] = []
    if flat:
        mat = np.asarray(flat, dtype=np.float32)
        mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        sims = mat @ q
        vscored = [(float(sims[s:e].max()), sid) for sid, s, e in spans]
    vscored.sort(reverse=True)
    vec_rank = {sid: i for i, (_s, sid) in enumerate(vscored)}
    # 字面臂：沿用現成的關鍵字搜尋當 lexical 訊號（多撈一些候選給融合用）
    lit = _search_sessions(keyword, limit=50)
    lit_rank = {e["session_id"]: i for i, e in enumerate(lit)}
    # RRF 融合：每個 session 分數 = 各臂 1/(K+名次) 相加；兩臂都上榜者自然被抬高
    K = 60
    fused: dict[str, float] = {}
    for sid, r in vec_rank.items():
        fused[sid] = fused.get(sid, 0.0) + 1.0 / (K + r)
    for sid, r in lit_rank.items():
        fused[sid] = fused.get(sid, 0.0) + 1.0 / (K + r)
    order = sorted(fused, key=lambda s: fused[s], reverse=True)[:limit]
    # 組結果（與字面搜尋結構一致）；片段優先用關鍵字臂命中的上下文，其次退回開頭
    titles = _load_titles()
    claude_home = Path.home() / ".claude" / "projects"
    lit_by_sid = {e["session_id"]: e for e in lit}
    results: list[dict] = []
    for sid in order:
        jf = next(claude_home.glob(f"*/{sid}.jsonl"), None)
        if jf is None:
            continue
        meta = _session_meta(jf)
        snippet = (lit_by_sid.get(sid, {}).get("snippet")
                   or _read_session_text(sid, max_chars=120) or meta.get("first_prompt") or "")
        results.append({
            "session_id": sid,
            "project_path": meta["cwd"] or str(DEFAULT_DIR),
            "mtime": jf.stat().st_mtime,
            "title": titles.get(sid) or meta["title"],
            "snippet": snippet.replace("\n", " ").strip()[:120],
        })
    return results

def _search_sessions(keyword: str, limit: int = 15) -> list[dict]:
    claude_home = Path.home() / ".claude" / "projects"
    if not claude_home.exists():
        return []
    kw = keyword.lower()
    hits: list[dict] = []
    for proj_dir in claude_home.iterdir():
        if not proj_dir.is_dir():
            continue
        for jf in proj_dir.glob("*.jsonl"):
            meta = _session_meta(jf)
            if not meta["is_bot"]:  # 只搜本 bot 自己的對話
                continue
            snippet = ""
            try:
                with jf.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if kw in line.lower():
                            try:
                                obj = json.loads(line)
                                txt = json.dumps(obj.get("message", obj), ensure_ascii=False)
                            except Exception:
                                txt = line
                            low = txt.lower()
                            pos = low.find(kw)
                            s = max(0, pos - 30)
                            snippet = txt[s:pos + 50].replace("\n", " ").strip()
                            break
            except Exception:
                continue
            if snippet:
                hits.append({
                    "session_id": jf.stem,
                    "project_path": meta["cwd"] or str(DEFAULT_DIR),
                    "mtime": jf.stat().st_mtime,
                    "title": meta["title"],
                    "snippet": snippet,
                })
    return sorted(hits, key=lambda e: e["mtime"], reverse=True)[:limit]

@bot.tree.command(name="search", description=t("cmd_search_desc"))
async def cmd_search(interaction: discord.Interaction, query: str) -> None:
    if not await check_auth(interaction): return
    await interaction.response.defer(ephemeral=True)   # ephemeral：零殘留
    # 優先語意搜尋（依描述內容找，需 fastembed）；無 fastembed 時自動退回字面關鍵字搜尋
    entries = await asyncio.to_thread(_semantic_search, query)
    if entries is None:
        entries = await asyncio.to_thread(_search_sessions, query)
    if not entries:
        await interaction.followup.send(t("search_none", kw=query), ephemeral=True)
        return
    options = []
    for i, e in enumerate(entries):
        dt = datetime.fromtimestamp(e["mtime"]).strftime("%m/%d %H:%M")
        label = (e["title"] or e["project_path"])[:95]
        desc = f"{dt}｜…{e['snippet'][:70]}"
        options.append(discord.SelectOption(label=label, description=desc[:100], value=str(i)))

    class SearchSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder=t("pick_restore"), options=options)
        async def callback(self, inter: discord.Interaction) -> None:
            # entries 綁在本 view 的 closure：多個選單同開也不會互相汙染
            await _restore_session_to_channel(inter, entries[int(self.values[0])])

    view = discord.ui.View()
    view.add_item(SearchSelect())
    await interaction.followup.send(t("search_header", kw=query, n=len(entries)), view=view, ephemeral=True)

@bot.tree.command(name="confirm", description=t("cmd_confirm_desc"))
@discord.app_commands.choices(switch=[
    discord.app_commands.Choice(name=t("confirm_switch_on"), value="on"),
    discord.app_commands.Choice(name=t("confirm_switch_off"), value="off"),
])
async def cmd_confirm(interaction: discord.Interaction, switch: str) -> None:
    if not await check_auth(interaction): return
    global _CONFIRM_ENABLED
    _CONFIRM_ENABLED = (switch == "on")
    await interaction.response.send_message(
        t("confirm_toggle_on") if _CONFIRM_ENABLED else t("confirm_toggle_off"))

@bot.tree.command(name="model", description=t("cmd_model_desc"))
@discord.app_commands.choices(model=[
    discord.app_commands.Choice(name=t("model_sonnet46"), value="claude-sonnet-4-6"),
    discord.app_commands.Choice(name="Sonnet 4.5",          value="claude-sonnet-4-5"),
    discord.app_commands.Choice(name="Opus 4.8",            value="claude-opus-4-8"),
    discord.app_commands.Choice(name=t("model_haiku"),   value="claude-haiku-4-5-20251001"),
    discord.app_commands.Choice(name=t("choice_default"),                 value="default"),
])
async def cmd_model(interaction: discord.Interaction, model: str) -> None:
    if not await check_auth(interaction): return
    st = get_state(interaction.channel_id)
    st.model = None if model == "default" else model
    _persist_session(st)   # 立刻存檔，重啟後仍記得
    await interaction.response.send_message(t("model_set", model=model))

@bot.tree.command(name="effort", description=t("cmd_effort_desc"))
@discord.app_commands.choices(effort=[
    discord.app_commands.Choice(name=t("effort_low"), value="low"),
    discord.app_commands.Choice(name="medium",      value="medium"),
    discord.app_commands.Choice(name="high",        value="high"),
    discord.app_commands.Choice(name="xhigh",       value="xhigh"),
    discord.app_commands.Choice(name=t("effort_max"), value="max"),
    discord.app_commands.Choice(name=t("choice_default"),        value="default"),
])
async def cmd_effort(interaction: discord.Interaction, effort: str) -> None:
    if not await check_auth(interaction): return
    st = get_state(interaction.channel_id)
    st.effort = None if effort == "default" else effort
    _persist_session(st)   # 立刻存檔，重啟後仍記得
    await interaction.response.send_message(t("effort_set", effort=effort))

@bot.tree.command(name="plan", description=t("cmd_plan_desc"))
@discord.app_commands.choices(plan=[
    discord.app_commands.Choice(name="Pro (~$20/mo)",          value="pro"),
    discord.app_commands.Choice(name="Max (~$100 or $200/mo)", value="max"),
    discord.app_commands.Choice(name="Team / Enterprise",      value="enterprise"),
    discord.app_commands.Choice(name="API / pay-as-you-go",    value="api"),
    discord.app_commands.Choice(name=t("plan_unknown"),        value="unknown"),
])
async def cmd_plan(interaction: discord.Interaction, plan: str) -> None:
    # 方案是帳號全域設定（影響所有頻道），限主帳號設定
    if not await check_auth(interaction, owner_only=True): return
    global _account_plan
    _account_plan = plan
    _save_plan(plan)   # 立刻存檔，重啟後仍記得
    # 依官方規則回報：這個方案下 Opus 會不會「自動」拿到 1M
    msg = t("plan_set_auto", plan=plan) if plan in _AUTO_1M_PLANS else t("plan_set_std", plan=plan)
    await interaction.response.send_message(msg)

@bot.tree.command(name="drive", description=t("cmd_drive_desc"))
@discord.app_commands.choices(mode=[
    discord.app_commands.Choice(name="on",  value="on"),
    discord.app_commands.Choice(name="off", value="off"),
])
async def cmd_drive(interaction: discord.Interaction, mode: str) -> None:
    # 開車模式是帳號全域開關（控制兩個 GPU 模型的生命週期），限主帳號設定
    if not await check_auth(interaction, owner_only=True): return
    # 開車核心是選配模組：drive_core.py 被移除時，/drive 直接回報未安裝、不當機
    if drive_core is None:
        await interaction.response.send_message(t("drive_unavailable"))
        return
    global _drive_mode
    if mode == "on":
        _drive_mode = True
        drive_core.save_drive(_DRIVE_FILE, True)   # 立刻存檔，重啟後自動恢復載入
        # 載入兩個模型耗時（首次還要下載），先即時回「載入中」避免互動 3 秒逾時
        await interaction.response.send_message(t("drive_on_loading"))
        await asyncio.to_thread(drive_core.get_whisper)
        try:
            await asyncio.to_thread(drive_core.get_f5tts)
        except Exception as e:
            # TTS 載入失敗：語音輸入仍可用，只是不能語音回覆（仍維持開車模式）
            await interaction.followup.send(t("drive_xtts_fail", ex=e))
            return
        await interaction.followup.send(t("drive_on_ready"))
    else:
        _drive_mode = False
        drive_core.save_drive(_DRIVE_FILE, False)
        # 卸載兩個模型、釋放 VRAM，回到純文字 bot
        await asyncio.to_thread(drive_core.unload_whisper)
        await asyncio.to_thread(drive_core.unload_f5tts)
        await interaction.response.send_message(t("drive_off"))

@bot.tree.command(name="cd", description=t("cmd_cd_desc"))
async def cmd_cd(interaction: discord.Interaction, path: str) -> None:
    if not await check_auth(interaction): return
    p = Path(path)
    if not p.exists() or not p.is_dir():
        await interaction.response.send_message(t("cd_not_found", path=path))
        return
    st = get_state(interaction.channel_id)
    st.cwd = p
    _persist_session(st)   # 立刻存檔，重啟後仍記得
    await interaction.response.send_message(t("cd_done", p=p))

@bot.tree.command(name="pwd", description=t("cmd_pwd_desc"))
async def cmd_pwd(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    cwd = state.cwd
    if state.wt:
        await interaction.response.send_message(
            t("pwd_with_wt", cwd=cwd, branch=state.wt["branch"]))
    else:
        await interaction.response.send_message(f"📂 `{cwd}`")

def _wt_error_text(error: str) -> str:
    """把 wt_core 回傳的錯誤代碼轉成給使用者的中文/英文訊息。"""
    mapping = {
        "not_a_repo": t("wt_err_not_repo"),
        "no_base_branch": t("wt_err_no_base"),
        "path_exists": t("wt_err_path_exists"),
    }
    return mapping.get(error, t("wt_err_git", err=error[:300]))

@bot.tree.command(name="worktree", description=t("cmd_worktree_desc"))
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="on", value="on"),
    discord.app_commands.Choice(name="merge", value="merge"),
    discord.app_commands.Choice(name="off", value="off"),
    discord.app_commands.Choice(name="list", value="list"),
])
async def cmd_worktree(interaction: discord.Interaction, action: str,
                       name: Optional[str] = None) -> None:
    """平行協作：on 開、merge 合回主分支並清理、off 移除（皆乾淨才動）、list 列出。"""
    if not await check_auth(interaction): return
    await interaction.response.defer()
    state = get_state(interaction.channel_id)
    cwd = state.cwd

    if action == "list":
        items = await asyncio.to_thread(wt_core.list_worktrees, cwd)
        if not items:
            await interaction.followup.send(t("wt_list_none"))
            return
        lines = [t("wt_list_title")]
        for it in items:
            br = it.get("branch") or (it.get("head", "")[:8]) or "?"
            lines.append(t("wt_list_item", branch=br, path=it.get("path", "")))
        await interaction.followup.send("\n".join(lines))
        return

    if action == "on":
        if state.wt:
            w = state.wt
            await interaction.followup.send(
                t("wt_already_on", branch=w["branch"], path=w["path"]))
            return
        seg = name or getattr(interaction.channel, "name", None) or "session"
        res = await asyncio.to_thread(wt_core.create, cwd, seg)
        if not res.ok:
            await interaction.followup.send(_wt_error_text(res.error))
            return
        state.wt = {
            "path": str(res.path),
            "branch": res.branch,
            "base": res.base,
            "repo": str(res.repo),
            "prev_cwd": str(cwd),   # off 時還原到啟用前的目錄
        }
        state.cwd = res.path
        _persist_session(state)
        await interaction.followup.send(
            t("wt_on_done", branch=res.branch, base=res.base, path=res.path))
        if state._sidebar:
            await _rename_for_wt(interaction.channel, True)
        return

    if action == "merge":
        w = state.wt
        if not w:
            await interaction.followup.send(t("wt_not_on"))
            return
        res = await asyncio.to_thread(
            wt_core.merge, w["repo"], w["path"], w["branch"], w["base"])
        if not res.ok:
            # 所有失敗情況都保持原狀、不刪任何東西（Q3-A 硬安全閘）
            if res.error == "worktree_dirty":
                await interaction.followup.send(t("wt_merge_wt_dirty"))
            elif res.error == "repo_dirty":
                await interaction.followup.send(
                    t("wt_merge_repo_dirty", base=w["base"]))
            elif res.error.startswith("repo_not_on_base"):
                cur = res.error.split(":", 1)[1] if ":" in res.error else "?"
                await interaction.followup.send(
                    t("wt_merge_not_on_base", base=w["base"], cur=cur))
            elif res.error == "merge_conflict":
                await interaction.followup.send(t("wt_merge_conflict",
                    branch=w["branch"], base=w["base"],
                    files=(res.detail or "?")[:500]))
            else:
                await interaction.followup.send(_wt_error_text(res.error))
            return
        # 合併成功 → worktree 與分支已清理，還原 cwd、清掉 wt、持久化
        prev = Path(w.get("prev_cwd") or w["repo"])
        state.cwd = prev if prev.is_dir() else DEFAULT_DIR
        state.wt = None
        _persist_session(state)
        await interaction.followup.send(
            t("wt_merge_done", branch=w["branch"], base=w["base"],
              cwd=state.cwd))
        if state._sidebar:
            await _rename_for_wt(interaction.channel, False)
        return

    # action == "off"
    w = state.wt
    if not w:
        await interaction.followup.send(t("wt_not_on"))
        return
    res = await asyncio.to_thread(wt_core.remove, w["repo"], w["path"])
    if not res.ok:
        # 多半是有未提交變更 → 安全閘擋下，工作保留
        await interaction.followup.send(t("wt_off_dirty", err=res.error[:300]))
        return
    prev = Path(w.get("prev_cwd") or w["repo"])
    state.cwd = prev if prev.is_dir() else DEFAULT_DIR
    state.wt = None
    _persist_session(state)
    await interaction.followup.send(
        t("wt_off_done", branch=w["branch"], cwd=state.cwd))
    if state._sidebar:
        await _rename_for_wt(interaction.channel, False)

@bot.tree.command(name="screenshot", description=t("cmd_screenshot_desc"))
async def cmd_screenshot(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    await interaction.response.defer()
    shot = await _capture_screenshot()
    if not shot:
        await interaction.followup.send(t("screenshot_failed"))
        return
    try:
        if shot.stat().st_size > _DISCORD_FILE_LIMIT:
            await interaction.followup.send(t("screenshot_too_large"))
        else:
            await interaction.followup.send(t("screenshot_caption"), file=discord.File(str(shot)))
    finally:
        try:
            shot.unlink()
        except Exception:
            pass

@bot.tree.command(name="usage", description=t("cmd_usage_desc"))
async def cmd_usage(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    await interaction.response.defer()
    data = await asyncio.get_event_loop().run_in_executor(None, fetch_usage)
    if not data:
        await interaction.followup.send(t("usage_unavailable"))
        return
    lines = [t("usage_title")]
    shown = False
    for key, label in [("five_hour", t("usage_5h")), ("seven_day", t("usage_7d")),
                        ("seven_day_sonnet", t("usage_7d_sonnet")), ("seven_day_opus", t("usage_7d_opus"))]:
        obj = data.get(key)
        if not obj: continue
        pct = obj.get("utilization")
        if pct is None:
            pct = obj.get("percent_used", 0)
        resets = obj.get("resets_at", "")
        lines.append(t("usage_line_reset", label=label, reset=_reset(resets), countdown=_countdown(resets)))
        lines.append(f"`[{_bar(pct)}]` {pct:.0f}%")
        shown = True
    if not shown:
        await interaction.followup.send(t("usage_empty"))
        return
    lines.append(t("usage_cache_note"))
    await interaction.followup.send("\n".join(lines))

@bot.tree.command(name="handoff", description=t("cmd_handoff_desc"))
async def cmd_handoff(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    state = get_state(interaction.channel_id)
    sid = state.session_id
    if not sid:
        await interaction.response.send_message(t("handoff_empty"), ephemeral=True)
        return
    await interaction.response.defer()
    await interaction.followup.send(t("handoff_generating"))
    doc = await _generate_handoff(sid, state.model)
    if not doc:
        await interaction.followup.send(t("handoff_empty"))
        return
    # send_long：短的直接貼成訊息（可複製）、長的存成 .md 上傳
    await send_long(interaction.channel, t("handoff_caption") + "\n\n" + doc)

@bot.tree.command(name="schedule", description=t("cmd_schedule_desc"))
async def cmd_schedule(interaction: discord.Interaction, task: str) -> None:
    if not await check_auth(interaction): return
    await interaction.response.defer()
    parse_prompt = t("schedule_parse_prompt", task=task)
    # 一次性解析：用 interaction.id 當合成頻道 id（不會與任何 channel_id 撞號），
    # 讓它自建臨時 client、不碰頻道的長駐 client；解析完在 finally 丟棄（A'）
    tmp_state = ChannelState(_cid=interaction.id, cwd=DEFAULT_DIR,
                             model="claude-haiku-4-5-20251001")
    try:
        result, _, _ = await run_claude(parse_prompt, tmp_state)
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if not json_match:
            await interaction.followup.send(t("schedule_parse_failed", result=result[:300]))
            return
        parsed = json.loads(json_match.group())
        schedule: dict = {
            "id": str(uuid.uuid4())[:8],
            "user_id": interaction.user.id,
            "channel_id": interaction.channel_id,
            "task": parsed.get("task", task),
            "cron": parsed.get("cron", ""),
            "next_run": parsed.get("next_run", ""),
            "created_at": datetime.now().isoformat(),
        }
        schedules = _load_schedules()
        schedules.append(schedule)
        _save_schedules(schedules)
        embed = discord.Embed(title=t("schedule_created_title"), color=discord.Color.blue())
        embed.add_field(name=t("field_task"), value=schedule["task"], inline=False)
        cron_display = f"`{schedule['cron']}`" if schedule["cron"] else t("once")
        embed.add_field(name=t("field_cron"), value=cron_display, inline=True)
        embed.add_field(name=t("field_next_run"), value=schedule["next_run"][:16] if schedule["next_run"] else t("unknown"), inline=True)
        embed.set_footer(text=f"ID: {schedule['id']}")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(t("schedule_create_failed", e=e))
    finally:
        await _drop_client(interaction.id)   # 關閉一次性解析用的臨時 client（A'）

@bot.tree.command(name="schedules", description=t("cmd_schedules_desc"))
async def cmd_schedules(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    schedules = _load_schedules()
    if not schedules:
        await interaction.response.send_message(t("schedules_none"), ephemeral=True)
        return
    lines = [t("schedules_title")]
    for s in schedules:
        cron_disp = s.get("cron") or t("once")
        next_disp = s.get("next_run","")[:16] or t("unknown")
        lines.append(t("schedule_line", id=s['id'], task=s['task'], next=next_disp, cron=cron_disp))

    view = discord.ui.View()
    for s in schedules[:5]:
        btn = discord.ui.Button(label=t("btn_delete", id=s['id']), style=discord.ButtonStyle.danger)
        async def del_cb(inter: discord.Interaction, sid: str = s["id"]) -> None:
            sched_list = _load_schedules()
            sched_list = [x for x in sched_list if x["id"] != sid]
            _save_schedules(sched_list)
            await inter.response.send_message(t("schedule_deleted", id=sid), ephemeral=True)
        btn.callback = del_cb
        view.add_item(btn)

    await interaction.response.send_message("\n".join(lines), view=view)

@bot.tree.command(name="adduser", description=t("cmd_adduser_desc"))
async def cmd_adduser(interaction: discord.Interaction, user: discord.Member) -> None:
    if not await check_auth(interaction, owner_only=True): return
    if user.id == ALLOWED_USER:
        await interaction.response.send_message(t("adduser_already_owner"), ephemeral=True)
        return
    _allowed_users.add(user.id)
    _save_allowed_users(_allowed_users)
    await interaction.response.send_message(t("adduser_done", mention=user.mention, id=user.id))

@bot.tree.command(name="removeuser", description=t("cmd_removeuser_desc"))
async def cmd_removeuser(interaction: discord.Interaction, user: discord.Member) -> None:
    if not await check_auth(interaction, owner_only=True): return
    if user.id == ALLOWED_USER:
        await interaction.response.send_message(t("removeuser_cant_owner"), ephemeral=True)
        return
    _allowed_users.discard(user.id)
    _save_allowed_users(_allowed_users)
    await interaction.response.send_message(t("removeuser_done", mention=user.mention, id=user.id))

@bot.tree.command(name="listusers", description=t("cmd_listusers_desc"))
async def cmd_listusers(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction, owner_only=True): return
    lines = [f"• `{uid}`{t('owner_tag') if uid == ALLOWED_USER else ''}" for uid in _allowed_users]
    await interaction.response.send_message(t("listusers_header") + "\n".join(lines), ephemeral=True)

@bot.tree.command(name="addchannel", description=t("cmd_addchannel_desc"))
async def cmd_addchannel(interaction: discord.Interaction) -> None:
    # 此指令本身不能用 check_auth（新頻道還沒在清單裡），改直接驗證主帳號
    if interaction.user.id != ALLOWED_USER:
        await interaction.response.send_message(t("owner_only"), ephemeral=True)
        return
    cid = interaction.channel_id
    if cid in _allowed_channels:
        await interaction.response.send_message(t("addchannel_already"), ephemeral=True)
        return
    _allowed_channels.add(cid)
    _save_allowed_channels(_allowed_channels)
    await interaction.response.send_message(
        t("addchannel_done"))

@bot.tree.command(name="removechannel", description=t("cmd_removechannel_desc"))
async def cmd_removechannel(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction, owner_only=True): return
    cid = interaction.channel_id
    _allowed_channels.discard(cid)
    _save_allowed_channels(_allowed_channels)
    await interaction.response.send_message(t("removechannel_done"))

@bot.tree.command(name="help", description=t("cmd_help_desc"))
async def cmd_help(interaction: discord.Interaction) -> None:
    if not await check_auth(interaction): return
    await interaction.response.send_message(t("help_text"))

# ── /guide 內建使用說明書 ─────────────────────────────────────────────────
_GUIDE_KEYS = ("basics", "sessions", "model", "voice", "files", "schedule", "worktree", "safety")

@bot.tree.command(name="guide", description=t("cmd_guide_desc"))
@discord.app_commands.choices(topic=[
    discord.app_commands.Choice(name=t(f"guide_topic_{k}"), value=k) for k in _GUIDE_KEYS
])
async def cmd_guide(interaction: discord.Interaction, topic: Optional[str] = None) -> None:
    """不帶主題給總覽、帶主題給該頁白話解說；ephemeral 只有本人看得到、不洗版。"""
    if not await check_auth(interaction): return
    key = f"guide_{topic}" if topic in _GUIDE_KEYS else "guide_overview"
    await interaction.response.send_message(t(key), ephemeral=True)

# ── 一般訊息 → 送給 Claude ──────────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if message.channel.id not in _allowed_channels or message.author.id not in _allowed_users:
        return
    if message.content.startswith("/"):   # slash 指令走 interaction 事件，不進 CC
        return

    if _processing.get(message.channel.id):
        await message.channel.send(t("busy_prev"), delete_after=5)
        return

    state = get_state(message.channel.id)
    text = re.sub(r"<@!?\d+>", "", message.content).strip()

    # 若有待答選項，使用者打數字 → 對應到選項文字
    pending = state.pending_options
    if pending and text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(pending):
            chosen = pending[idx]
            state.pending_options = None
            await _process_answer(message.channel, chosen, state)
            return

    _processing[message.channel.id] = True
    is_voice = False  # 這則是否來自語音輸入（決定要不要語音回覆）

    # 處理附件
    if message.attachments:
        tmp_dir = Path(__file__).parent / "tmp"
        tmp_dir.mkdir(exist_ok=True)
        saved: list[str] = []
        failed: list[str] = []
        voice_texts: list[str] = []
        voice_blocked = False  # 開車模式關閉時收到語音 → 標記，迴圈後提示
        for att in message.attachments:
            # 檔名加短隨機前綴：不同訊息／頻道同時上傳同名檔案時不互相覆蓋（tmp 為共用目錄）
            dest = tmp_dir / f"{uuid.uuid4().hex[:6]}_{att.filename}"
            # 語音訊息 → 本機 STT 轉文字（CC 讀不了音訊），不當檔案路徑給 CC
            if (att.content_type or "").startswith("audio/"):
                if not _drive_mode or drive_core is None:
                    voice_blocked = True  # 在家關閉中（或未裝開車模組），不載模型、不轉錄
                    continue
                try:
                    await att.save(dest)   # discord.py 原生非同步下載，不阻塞 event loop
                    transcript = await asyncio.to_thread(drive_core.transcribe, str(dest), t("stt_prompt"))
                    if transcript:
                        voice_texts.append(transcript)
                except Exception as ex:
                    failed.append(t("voice_fail", filename=att.filename, ex=ex))
                finally:
                    dest.unlink(missing_ok=True)  # 語音輸入音檔只需轉錄一次，用完即刪，不堆積
                continue
            try:
                await att.save(dest)   # discord.py 原生非同步下載，不阻塞 event loop
                # 簡報轉 PDF 後給 CC 視覺讀取；轉檔失敗則回退原檔
                if dest.suffix.lower() in (".ppt", ".pptx"):
                    pdf = await _convert_pptx(dest)
                    saved.append(str(pdf) if pdf else str(dest))
                else:
                    saved.append(str(dest))
            except Exception as ex:
                failed.append(t("attach_fail_item", filename=att.filename, ex=ex))
        # 語音辨識結果當成使用者打的字
        if voice_texts:
            is_voice = True
            heard = " ".join(voice_texts)
            await message.channel.send(t("heard", heard=heard))
            # 顯示給使用者的是乾淨原文；送給 CC 的另外包一層提示，
            # 標明這是語音辨識結果、可能有怪字，請 CC 依上下文推斷原意
            text = (text + " " if text else "") + t("voice_hint", heard=heard)
        # 開車模式關閉時收到語音 → 提示要先開（文字附件仍照常處理）
        if voice_blocked and not voice_texts:
            await message.channel.send(t("drive_off_voice"))
        if failed and not saved and not text:
            await message.channel.send(t("attach_failed", failed=', '.join(failed)))
            _processing[message.channel.id] = False
            return
        if saved:
            paths = "\n".join(saved)
            text = (text + "\n\n" if text else "") + t("uploaded_files", paths=paths)

    if not text:
        _processing[message.channel.id] = False
        return

    state.pending_options = None

    # Speaker ID：告知 CC 是誰在說話
    speaker = message.author.display_name
    full_prompt = f"[{speaker}]: {text}"
    _append_user_ledger(message.channel.id, speaker, text)   # 原始訊息落地存檔，供日後查證用

    # Auto-compact：context 達門檻先壓縮
    await _maybe_auto_compact(message.channel, state)

    # 目前 session 標題，顯示在思考中訊息頂部，工作時一眼知道在哪個對話
    state._session_label = await asyncio.to_thread(_session_label, state.session_id)

    progress_msg = await message.channel.send(t("thinking"))
    t0 = time.time()
    try:
        reply, new_sid, ask_data = await _run_tracked(
            message.channel.id, full_prompt, state, progress_msg
        )
        if new_sid:
            state.session_id = new_sid
            _persist_session(state)
            # 更新標題與 bot 狀態（新對話此時才有 session 檔可讀標題）
            state._session_label = await asyncio.to_thread(_session_label, new_sid)
            await _update_presence(message.channel.id, state._session_label)
        await progress_msg.delete()
        if ask_data:
            # 先送出「問題前的說明文字（思考結果）」，再送問題按鈕；
            # 否則使用者只會看到問題按鈕、看不到上方的說明文字
            if reply and reply != _NO_RESPONSE:
                reply = await _voice_reply(message.channel, reply, speak=(is_voice and _drive_mode))
                await _send_files_and_text(message.channel, reply)
            await _send_ask_question(message.channel, ask_data, state)
        elif reply and reply != _NO_RESPONSE:
            # 開車模式＋這則是語音輸入 → 解析朗讀版、合成語音檔；回傳去掉標記的文字版
            reply = await _voice_reply(message.channel, reply, speak=(is_voice and _drive_mode))
            await _send_files_and_text(message.channel, reply)
        # 長任務完成 → @使用者推播（手機會震，可離開後再回來）
        elapsed = time.time() - t0
        if elapsed >= NOTIFY_AFTER_SEC:
            tip = t("notify_need_answer") if ask_data else t("notify_done")
            await message.channel.send(f"{message.author.mention} ✅ {tip} · {elapsed:.0f}s")
        # 側欄頻道：第一句處理完 → 讀內容生成中文標題、改頻道名（只改一次）
        if state._sidebar and not state._named and state.session_id:
            state._named = True
            asyncio.create_task(_autoname_channel(message.channel, state))
        # 側欄頻道：最新有活動 → 移到入口下方置頂（已在頂端不動）
        elif state._sidebar:
            asyncio.create_task(_bump_channel_to_top(message.channel))
    except _StoppedByUser:
        await progress_msg.edit(content=t("stopped"))
    except CCError as e:
        await _handle_cc_error(progress_msg, e, state)
        if time.time() - t0 >= NOTIFY_AFTER_SEC:
            await message.channel.send(t("notify_error", mention=message.author.mention))
    except Exception as e:
        print(f"[CC_FAIL] kind=UNCLASSIFIED raw={e!r}", flush=True)
        detail = f"{type(e).__name__}: {e}"[:1500]
        await progress_msg.edit(content=t("unexpected_error", detail=detail)[:2000])
    finally:
        _processing[message.channel.id] = False

def _acquire_single_instance_lock() -> Optional["socket.socket"]:
    """單一實例防呆：綁定固定本機 port，綁不上代表已有 bot 在跑，回傳 None。
    用 socket 而非鎖檔，進程結束 port 自動釋放，不會有殘留鎖問題。"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 47361))  # 此 port 專供本 bot 當鎖用
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None

if __name__ == "__main__":
    _lock = _acquire_single_instance_lock()
    if _lock is None:
        print(t("instance_running"), flush=True)
        raise SystemExit(0)
    bot.run(DISCORD_TOKEN)
