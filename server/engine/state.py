"""對話狀態與三層設定。

沿用 cc-bot 的 ChannelState 設計，但拿掉所有 Discord 專屬欄位
（_live_msg / _sidebar / _named），並把 channel id(int) 換成 conv_id(str)。

**持久化的唯一真相在記憶體，檔案只是 write-through。** 2026-09-02 之前這裡是
「每次都讀整份檔→改一筆→整份寫回」，而讀檔任何失敗都回空 dict：`load_history`／
`search`／`scan_sessions` 在別的執行緒讀同一個 `session.json`，主執行緒同時
`os.replace` 換檔的那一瞬間讀到 Windows 的 PermissionError → 下一次 persist 就把
**所有對話的 session 對應**寫成只剩這一條。`util.read_text_with_retry` 為了同一件事
早就寫好、八個模組在用，唯獨這裡沒用。現在：第一次用時讀一次（撞鎖就重試，真的
壞掉才隔離），之後只改記憶體並整份寫出，讀失敗**絕不**寫回。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import config
from util import atomic_write_text, read_text_with_retry

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ConvState:
    """一個對話的會話狀態。

    slots=True：欄位名打錯（讀或寫）都立刻 AttributeError（fail-loud），
    不會像 dict 那樣靜默回 None 或長出殭屍鍵。
    底線開頭為執行期旗標，不進持久化內容。
    """

    conv_id: str                              # 對話 id
    cwd: Path                                 # 工作目錄
    session_id: str | None = None             # CC session id（None＝尚未開始）
    # 這條對話是接管來的話，記下原始那個 session 的 id。接管接的是複印本
    # （見 sessions.fork_session），只比對 session_id 的話原始那筆會重新出現在
    # 「電腦上的 session」清單裡，看起來像沒接管成功。
    forked_from: str | None = None
    model: str | None = None                  # 對話覆寫模型（None＝跟隨帳號預設）
    effort: str | None = None                 # 對話覆寫思考程度（None＝跟隨帳號預設）
    ctx_tokens: int = 0                       # 最近一次 result 回報的 context 用量
    # 下面兩個是每回合結束時向 SDK 問來的權威值（見 runner 的 get_context_usage）。
    # 不進持久化：它們是模型屬性、每回合都會重問，重啟後第一回合前先用估算頂著。
    ctx_max: int = 0                          # 這條對話真正的 context 上限（0＝還沒問到）
    ctx_threshold: int = 0                    # CLI 自己會啟動 auto-compact 的門檻
    _no_think: bool = False                   # 本次是否關閉思考（空回覆重試逃生門）


# ── 帳號預設（三層設定的中間層）──────────────────────────────────────────────
# 優先序：對話覆寫（ConvState.model/effort）→ 帳號預設（此處）→ 內建後備（config）。
_DEFAULTS_FILE = config.DATA_DIR / "account_defaults.json"


def _load_defaults() -> tuple[str | None, str | None]:
    try:
        d = json.loads(_DEFAULTS_FILE.read_text(encoding="utf-8"))
        return d.get("model"), d.get("effort")
    except Exception:
        return None, None


default_model, default_effort = _load_defaults()


def save_defaults(model: str | None, effort: str | None) -> None:
    """設定帳號預設並落檔。改了之後，未單獨覆寫的對話下次會自動重建 client。"""
    global default_model, default_effort
    default_model, default_effort = model, effort
    try:
        atomic_write_text(
            _DEFAULTS_FILE,
            json.dumps({"model": model, "effort": effort}, ensure_ascii=False),
        )
    except Exception:
        pass


def eff_model(state: ConvState) -> str:
    """該對話實際生效的模型：對話覆寫 → 帳號預設 → 內建後備。"""
    return state.model or default_model or config.DEFAULT_MODEL


def eff_effort(state: ConvState) -> str | None:
    """該對話實際生效的思考程度：對話覆寫 → 帳號預設（None＝SDK 預設）。"""
    return state.effort or default_effort


# ── 兩份 JSON 的讀寫（session 對應與標題共用）──────────────────────────────────
def _read_json_map(path: Path) -> dict:
    """讀一份 JSON dict。檔案不存在＝空；撞鎖就重試；真的壞掉才隔離。

    三種失敗要分開對待，混在一起就是資料遺失的來源：
    - 不存在：第一次啟動，空的沒問題。
    - PermissionError（重試到底仍失敗）：往上拋，讓這次操作失敗。**絕不能**回空
      dict——呼叫端接著寫回去就等於把整份檔清掉。
    - JSON 壞掉：把壞檔改名留證據（`.corrupt-時間戳`），從空的開始。內容已經
      讀不回來了，留著只會讓每一次讀都炸。
    """
    if not path.exists():
        return {}
    text = read_text_with_retry(path)
    try:
        data = json.loads(text)
    except ValueError:
        quarantine = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.replace(quarantine)
        except OSError:
            pass
        log.error("%s 不是合法 JSON，已隔離到 %s，從空的開始", path.name, quarantine.name)
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_map(path: Path, data: dict, **dump_kw) -> None:
    try:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, **dump_kw))
    except Exception:
        # 寫不進去不能悄悄吞：記憶體裡的真相還在，下次 persist 會再試，
        # 但至少要在 log 留一行，不然「重啟後設定變回預設」永遠查不到原因
        log.exception("寫入 %s 失敗", path.name)


# ── Session 持久化 ───────────────────────────────────────────────────────────
_map: dict | None = None      # conv_id → 持久化欄位；None＝還沒從檔案載入


def _load_map() -> dict:
    """conv_id → 持久化紀錄。第一次呼叫才讀檔，之後回記憶體那份。

    給 history／sessions／search 唯讀用的也是這一份——它們在別的執行緒跑，
    讀一個只在事件迴圈上被改的 dict 沒有問題；先前它們各自讀檔，才有撞鎖的機會。
    """
    global _map
    if _map is None:
        _map = _read_json_map(config.SESSION_FILE)
    return _map


def _flush_map() -> None:
    _write_json_map(config.SESSION_FILE, _load_map())


def persist(state: ConvState) -> None:
    """整包存：session_id + model/effort/cwd，重啟後設定不會變回預設。"""
    _load_map()[state.conv_id] = {
        "session_id": state.session_id,
        "forked_from": state.forked_from,
        "model": state.model,
        "effort": state.effort,
        "cwd": str(state.cwd or config.DEFAULT_CWD),
    }
    _flush_map()


_states: dict[str, ConvState] = {}


def get_state(conv_id: str) -> ConvState:
    """取得對話狀態：記憶體 → 硬碟 → 新建。"""
    st = _states.get(conv_id)
    if st is not None:
        return st
    rec = _load_map().get(conv_id) or {}
    cwd = Path(rec.get("cwd") or config.DEFAULT_CWD)
    if not cwd.is_dir():
        cwd = config.DEFAULT_CWD
    st = ConvState(
        conv_id=conv_id,
        cwd=cwd,
        session_id=rec.get("session_id"),
        forked_from=rec.get("forked_from"),
        model=rec.get("model"),
        effort=rec.get("effort"),
    )
    _states[conv_id] = st
    return st


# ── 標題 ─────────────────────────────────────────────────────────────────────
_TITLES_FILE = config.DATA_DIR / "titles.json"
_titles: dict[str, str] | None = None


def _load_titles() -> dict[str, str]:
    global _titles
    if _titles is None:
        _titles = _read_json_map(_TITLES_FILE)
    return _titles


def get_title(conv_id: str) -> str | None:
    return _load_titles().get(conv_id)


def set_title(conv_id: str, title: str) -> None:
    _load_titles()[conv_id] = title
    _write_json_map(_TITLES_FILE, _load_titles(), indent=1)


def reset_cache() -> None:
    """丟掉記憶體裡的兩份快取，下次用時重新讀檔。只給測試與換 DATA_DIR 用。"""
    global _map, _titles
    _map = None
    _titles = None
    _states.clear()


# ── 對話列表 ─────────────────────────────────────────────────────────────────
def list_conversations() -> list[dict]:
    """列出所有已知對話（記憶體＋硬碟合併），新到舊。

    最後活動時間以 session jsonl 的 mtime 為準——那是唯一跨重啟仍準確的來源。
    """
    titles = _load_titles()
    known: dict[str, dict] = {}
    for cid, rec in _load_map().items():
        known[cid] = {"conv_id": cid, "session_id": rec.get("session_id")}
    for cid, st in _states.items():
        known.setdefault(cid, {"conv_id": cid, "session_id": st.session_id})

    out = []
    claude_home = config.claude_projects_dir()
    for cid, rec in known.items():
        mtime = 0.0
        sid = rec.get("session_id")
        if sid:
            for jf in claude_home.glob(f"*/{sid}.jsonl"):
                try:
                    mtime = jf.stat().st_mtime
                except OSError:
                    pass
                break
        out.append({
            "conv_id": cid,
            "title": titles.get(cid) or cid,
            "mtime": mtime,
            "has_session": bool(sid),
        })
    return sorted(out, key=lambda e: e["mtime"], reverse=True)


def delete_conversation(conv_id: str) -> bool:
    """刪除一個對話的狀態與標題。session jsonl 檔刻意不動——那是 CC 的資料。"""
    existed = conv_id in _states
    _states.pop(conv_id, None)
    data = _load_map()
    if conv_id in data:
        existed = True
        data.pop(conv_id)
        _flush_map()
    titles = _load_titles()
    if conv_id in titles:
        titles.pop(conv_id)
        _write_json_map(_TITLES_FILE, titles, indent=1)
    return existed
