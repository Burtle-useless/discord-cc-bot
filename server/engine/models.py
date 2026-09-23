"""模型清單：官方 `/v1/models` 為主、CLI 的 initialize 清單為輔。給設定頁與驗證用。

**清單要自己長，不必有人補。** 先前只抄 CLI initialize 回應裡的 `models`，而那份清單
跟著 CLI 版本走：官方發了新模型、CLI 還沒升版，清單上就沒有，只能在這裡手動補一筆。
現在主來源是官方的 `/v1/models`（用 Claude Code 自己的登入去問，見 oauth_api）：

    {"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
     "created_at": "2026-09-21T16:24:00Z", "max_input_tokens": 1000000,
     "capabilities": {"effort": {"supported": true, "low": {"supported": true}, ...}}}

這份是帳號當下能用的完整清單（新模型發佈當天就在上面），而且每顆都帶支援的思考等級。

CLI 的清單（`get_server_info()` 回的 `models`，每顆有 `value`／`resolvedModel`／
`description`）退居輔助，只提供兩件官方清單沒有的事：
- 哪些系列預設開 1M context（它列 `opus[1m]` 但 `sonnet` 不帶——方案決定的，
  官方清單的 `max_input_tokens` 只說模型撐得到多大，不說這個方案能不能用）；
- 別名（`opus[1m]`、`default`…）現在解析成哪一顆，舊資料存的是別名時要對得回來。

**官方清單上有、CLI 卻跑不動的模型**：後端會擋太舊的 CLI（「version 2.1.280 or newer is
required」）。比 CLI 自己清單裡最新那顆還新的模型，各實際試跑一次（`probe_new`），
跑不動就標出要哪一版，App 把它灰掉、指去工具頁更新（sdk_update）。

**快取落檔**：兩份清單都寫進 `data/models.json`，啟動時先讀它頂著。都沒有時退回一份
只有別名的最小清單，只保證設定頁不是空的。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import config
from util import atomic_write_text, read_text_with_retry

from . import oauth_api

log = logging.getLogger(__name__)

CATALOG_FILE = config.DATA_DIR / "models.json"
MODELS_URL = "https://api.anthropic.com/v1/models?limit=100"

# 已知的思考等級，照強度排。沒標 supportsEffort 的模型（例如 haiku）送了 CLI 也不會
# 出錯，只是會被忽略。官方哪天多一級，清單裡有就照樣收（見 is_effort_known）。
ALL_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# 一次性 meta 查詢（生標題）用的小模型。用官方別名：CLI 自己解析成當下最新的 Haiku，
# 換代不必改。不跟帳號預設走——預設換成 Opus 就是拿 Opus 生 20 字標題。
META_MODEL: str = "haiku"

# 多久重新要一次。清單只在官方發佈或 CLI 升版時變，一小時問一次綽綽有餘。
REFRESH_INTERVAL_SEC = 3600.0
# 官方清單拿失敗（斷網、登入過期）時多久再試
API_RETRY_SEC = 300.0

# 系列 → 一句中文說明。跟系列走、不跟型號走：同系列出新版不必改這裡；
# 冒出新系列就退回 CLI 那句英文（沒有就空白），不影響能不能選。
_TAGLINE: dict[str, str] = {
    "opus": "日常與複雜任務的主力",
    "fable": "最強，適合最難、最長的任務",
    "sonnet": "有效率，適合例行工作",
    "haiku": "最快，適合簡短問答",
}

_FAMILY_RE = re.compile(r"^claude-([a-z]+)-")
_REQUIRES_RE = re.compile(r"version (\d+(?:\.\d+)+) or newer is required", re.IGNORECASE)


@dataclass(frozen=True)
class ModelInfo:
    value: str                       # 給 options.model 用的值（完整 id，必要時帶 [1m]）
    resolved: str                    # 官方 id（不帶 [1m]），記帳與比對用
    name: str                        # 顯示名（Opus 5.5）
    description: str = ""
    efforts: tuple[str, ...] = ALL_EFFORTS
    supports_effort: bool = True
    family: str = ""                 # opus / fable / sonnet / haiku
    tier: str = "main"               # main＝各系列最新那顆；more＝收進「更多模型」
    context: int = 0                 # 模型撐得到的 context（官方清單給的）
    released: str = ""               # 發布日 YYYY-MM-DD
    aliases: tuple[str, ...] = ()    # 其他會對到這顆的值（CLI 別名、帶不帶 [1m] 的 id）
    available: bool = True           # False＝現在的 CLI 跑不動
    requires_cli: str = ""           # 跑不動的話要哪一版 CLI（知道的話）

    def to_wire(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "resolved": self.resolved,
            "name": self.name,
            "description": self.description,
            "efforts": list(self.efforts),
            "supports_effort": self.supports_effort,
            "family": self.family,
            "tier": self.tier,
            "context": self.context,
            "released": self.released,
            "aliases": list(self.aliases),
            "available": self.available,
            "requires_cli": self.requires_cli,
        }


# 兩份清單都還沒有時的後備。只列別名，讓 CLI 自己解析。
_FALLBACK: tuple[ModelInfo, ...] = (
    ModelInfo("opus[1m]", "", "Opus", _TAGLINE["opus"], family="opus"),
    ModelInfo("sonnet", "", "Sonnet", _TAGLINE["sonnet"], family="sonnet"),
    ModelInfo("haiku", "", "Haiku", _TAGLINE["haiku"], efforts=(), supports_effort=False,
              family="haiku"),
)


@dataclass
class _Catalog:
    cli: list[ModelInfo] = field(default_factory=list)       # CLI initialize 的清單
    api: list[dict[str, Any]] = field(default_factory=list)  # 官方清單（精簡過的欄位）
    fetched_at: float = 0.0          # CLI 清單最後拿到的時刻（epoch）
    api_at: float = 0.0              # 官方清單最後拿到的時刻
    api_tried: float = 0.0           # 官方清單最後一次去拿（成功與否）的時刻
    probes: dict[str, dict[str, Any]] = field(default_factory=dict)  # id → 試跑結果
    source: str = "fallback"         # fallback / cache / cli / api


_cat = _Catalog()
_tasks: set[asyncio.Task[Any]] = set()   # 背景工作要留參照，不然可能跑到一半被回收


def family(model_id: str) -> str:
    m = _FAMILY_RE.match(model_id or "")
    return m.group(1) if m else ""


def base_id(value: str) -> str:
    """去掉 `[1m]` 之類的後綴。"""
    return (value or "").split("[", 1)[0]


# ── 解析兩種來源 ──────────────────────────────────────────────────────────────

def _parse_cli(raw: Any) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in raw or []:
        if not isinstance(m, dict) or not m.get("value"):
            continue
        supports = bool(m.get("supportsEffort"))
        levels = tuple(str(x) for x in (m.get("supportedEffortLevels") or ()))
        out.append(ModelInfo(
            value=str(m["value"]),
            resolved=str(m.get("resolvedModel") or ""),
            name=str(m.get("displayName") or m["value"]),
            description=str(m.get("description") or ""),
            efforts=levels if supports else (),
            supports_effort=supports,
        ))
    return out


def _parse_api(raw: Any) -> list[dict[str, Any]]:
    """官方回應 → 精簡的 dict（也是快取檔裡的樣子）。"""
    out: list[dict[str, Any]] = []
    for m in raw or []:
        if not isinstance(m, dict) or not str(m.get("id") or "").startswith("claude-"):
            continue
        eff = (m.get("capabilities") or {}).get("effort") or {}
        if "efforts" in m:                          # 已經是快取裡的精簡格式
            levels = [str(x) for x in m.get("efforts") or []]
        elif eff.get("supported"):
            levels = [k for k, v in eff.items()
                      if k != "supported" and isinstance(v, dict) and v.get("supported")]
        else:
            levels = []
        out.append({
            "id": str(m["id"]),
            "name": str(m.get("display_name") or m.get("name") or m["id"]),
            "created": str(m.get("created_at") or m.get("created") or ""),
            "context": int(m.get("max_input_tokens") or m.get("context") or 0),
            "efforts": levels,
        })
    return out


def _cli_tagline(cli: list[ModelInfo], fam: str) -> str:
    """CLI 對這個系列的說明（「Opus 5 with 1M context · Best for …」取點後面那句）。"""
    for c in cli:
        if family(base_id(c.resolved or c.value)) == fam and c.description:
            return c.description.split("·")[-1].strip()
    return ""


def _merge(api: list[dict[str, Any]], cli: list[ModelInfo],
           probes: dict[str, dict[str, Any]], cli_ver: str) -> list[ModelInfo]:
    """官方清單為骨架，CLI 清單補 1M 設定、別名、系列順序；再標上試跑結果。"""
    cli_by_id: dict[str, list[str]] = {}
    one_m: set[str] = set()
    fam_order: list[str] = []
    for c in cli:
        bid = base_id(c.resolved) or base_id(c.value)
        cli_by_id.setdefault(bid, []).append(c.value)
        fam = family(bid)
        if not fam:
            continue
        if fam not in fam_order:
            fam_order.append(fam)
        if c.value.endswith("[1m]") or c.resolved.endswith("[1m]"):
            one_m.add(fam)

    ordered = sorted(api, key=lambda a: a["created"], reverse=True)
    newest: dict[str, str] = {}
    for a in ordered:
        newest.setdefault(family(a["id"]) or a["id"], a["id"])

    main: list[ModelInfo] = []
    more: list[ModelInfo] = []
    for a in ordered:
        mid, fam = a["id"], family(a["id"])
        is_main = newest.get(fam or mid) == mid
        value = f"{mid}[1m]" if fam in one_m and a["context"] >= 1_000_000 else mid
        aliases = ({mid, f"{mid}[1m]"} | set(cli_by_id.get(mid, []))) - {value}
        created = a["created"][:10]
        if is_main:
            desc = _TAGLINE.get(fam) or _cli_tagline(cli, fam)
        else:
            desc = f"{created[:4]} 年 {int(created[5:7])} 月發布" if len(created) >= 7 else ""
        p = probes.get(mid)
        blocked = bool(p and p.get("cli") == cli_ver and not p.get("ok"))
        info = ModelInfo(
            value=value, resolved=mid,
            name=a["name"].removeprefix("Claude ").strip() or mid,
            description=desc,
            efforts=tuple(a["efforts"]), supports_effort=bool(a["efforts"]),
            family=fam, tier="main" if is_main else "more",
            context=a["context"], released=created,
            aliases=tuple(sorted(aliases)),
            available=not blocked,
            requires_cli=str(p.get("requires") or "") if blocked and p else "",
        )
        (main if is_main else more).append(info)

    def fam_rank(m: ModelInfo) -> int:
        return fam_order.index(m.family) if m.family in fam_order else len(fam_order)

    main.sort(key=fam_rank)       # 同名次的保持新到舊（sort 是穩定的）
    return main + more


# ── 快取 ─────────────────────────────────────────────────────────────────────

def _load_cache() -> None:
    if not CATALOG_FILE.exists():
        return
    try:
        data = json.loads(read_text_with_retry(CATALOG_FILE))
        cli = _parse_cli(data.get("models"))
        api = _parse_api(data.get("api_models"))
        probes = data.get("probes") or {}
    except Exception:  # noqa: BLE001 — 快取壞了就當沒有，下次連線會重寫
        log.warning("models.json 讀不出來，先用內建清單")
        return
    if cli:
        _cat.cli = cli
        _cat.fetched_at = float(data.get("fetched_at") or 0.0)
    if api:
        _cat.api = api
        _cat.api_at = float(data.get("api_fetched_at") or 0.0)
    if isinstance(probes, dict):
        _cat.probes = {str(k): v for k, v in probes.items() if isinstance(v, dict)}
    if cli or api:
        _cat.source = "cache"


def _save() -> None:
    try:
        atomic_write_text(CATALOG_FILE, json.dumps({
            "fetched_at": _cat.fetched_at,
            "models": [
                {
                    "value": m.value, "resolvedModel": m.resolved, "displayName": m.name,
                    "description": m.description, "supportsEffort": m.supports_effort,
                    "supportedEffortLevels": list(m.efforts),
                }
                for m in _cat.cli
            ],
            "api_fetched_at": _cat.api_at,
            "api_models": _cat.api,
            "probes": _cat.probes,
        }, ensure_ascii=False, indent=1))
    except Exception:  # noqa: BLE001
        log.warning("models.json 寫入失敗", exc_info=True)


_load_cache()


# ── 查詢 ─────────────────────────────────────────────────────────────────────

def _cli_version() -> str:
    """CLI 版本。第一次要開子行程問，會卡一下——async 的地方包 to_thread。"""
    from . import sdk_update
    return sdk_update.cli_version()


def catalog() -> list[ModelInfo]:
    """目前知道的模型清單：官方清單 → CLI 清單 → 內建後備，第一個有的就用。

    試跑結果只認「同一版 CLI」試出來的。版本還沒問過（剛啟動、bootstrap 還沒跑到）
    就先不標，不在這裡開子行程卡住事件迴圈。"""
    if _cat.api:
        from . import sdk_update
        return _merge(_cat.api, _cat.cli, _cat.probes, sdk_update.cli_version_cached())
    return list(_cat.cli) if _cat.cli else list(_FALLBACK)


def values() -> list[str]:
    return [m.value for m in catalog()]


def find(value: str | None) -> ModelInfo | None:
    """值 → 清單裡的那顆。認得：清單的值、官方 id、別名（`opus[1m]`／`default`），
    以及只寫系列名的（`opus`＝該系列最新那顆，跟 CLI 的解析一致）。"""
    if not value:
        return None
    cat = catalog()
    bid = base_id(value)
    for m in cat:
        if value == m.value or value in m.aliases or (m.resolved and bid == base_id(m.resolved)):
            return m
    # CLI 別名（`default`、還沒被收進 aliases 的）照 CLI 清單解析成實際那顆
    for c in _cat.cli:
        if value == c.value and c.resolved and base_id(c.resolved) != bid:
            return find(base_id(c.resolved))
    # 只寫系列名：該系列的主力那顆
    return next((m for m in cat if m.family and m.family == bid and m.tier == "main"), None)


def is_known(value: str) -> bool:
    """設定端點用的驗證：清單找得到的值，或任何 claude- 開頭的 id
    （舊資料存的是完整 id；CLI 自己會擋真的不存在的模型）。"""
    return find(value) is not None or value.startswith("claude-")


def is_effort_known(effort: str) -> bool:
    return effort in ALL_EFFORTS or any(effort in m.efforts for m in catalog())


def resolve(value: str) -> str:
    """把別名換成實際的模型 id（記帳用）；認不得的原樣回。"""
    m = find(value)
    return (m.resolved or value) if m else value


def efforts_for(value: str | None) -> list[str]:
    """這個模型能選的思考等級。清單裡標了就照標的，認不得的一律給完整清單。"""
    m = find(value)
    if m is None:
        return list(ALL_EFFORTS)
    return list(m.efforts) if m.supports_effort else []


def to_wire() -> list[dict[str, Any]]:
    return [m.to_wire() for m in catalog()]


def source() -> str:
    return _cat.source


def needs_refresh() -> bool:
    return time.time() - _cat.fetched_at >= REFRESH_INTERVAL_SEC


def api_stale() -> bool:
    now = time.time()
    if now - _cat.api_tried < (API_RETRY_SEC if _cat.api_at < _cat.api_tried else 0):
        return False                               # 剛失敗過，等一下再試
    return now - _cat.api_at >= REFRESH_INTERVAL_SEC


# ── 更新 ─────────────────────────────────────────────────────────────────────

def refresh_api() -> bool:
    """向官方要一次清單（同步、會打網路，呼叫端丟 to_thread）。拿到就更新＋落檔。"""
    _cat.api_tried = time.time()
    data = oauth_api.get_json(MODELS_URL, headers={"anthropic-version": "2023-06-01"})
    api = _parse_api((data or {}).get("data") if isinstance(data, dict) else None)
    if not api:
        log.warning("向官方要模型清單失敗，沿用現有的")
        return False
    _cat.api = api
    _cat.api_at = time.time()
    _cat.source = "api"
    _save()
    return True


async def refresh_from(client: Any) -> bool:
    """向一個已連線的 client 要一次 CLI 清單，順便排一次官方清單的更新。

    呼叫端（client_pool）在每次建立連線後叫；這裡自己控制頻率，一小時內拿過就直接
    跳過，不打控制通道。永不拋例外——拿不到清單不能讓對話回合失敗。
    """
    schedule_refresh()
    if not needs_refresh():
        return False
    try:
        info = await client.get_server_info()
        cli = _parse_cli((info or {}).get("models"))
    except Exception:  # noqa: BLE001
        log.warning("向 CLI 要模型清單失敗，沿用現有的", exc_info=True)
        return False
    if not cli:
        return False
    _cat.cli = cli
    _cat.fetched_at = time.time()
    if not _cat.api:
        _cat.source = "cli"
    _save()
    return True


def schedule_refresh() -> None:
    """官方清單過期了就在背景更新一次（接著試跑新模型）。不等、不拋。

    掛在「連上 CLI」與「打開設定頁」這兩個時機：這個服務與共用引擎的其他前端都會經過，
    不必各自再開一個排程。"""
    if not api_stale() or any(not t.done() for t in _tasks):
        return
    try:
        t = asyncio.get_running_loop().create_task(_refresh_and_probe())
    except RuntimeError:
        return
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _refresh_and_probe() -> None:
    try:
        if await asyncio.to_thread(refresh_api):
            await probe_new()
    except Exception:  # noqa: BLE001
        log.warning("背景更新模型清單失敗", exc_info=True)


def _probe_candidates(cli_ver: str) -> list[ModelInfo]:
    """要試跑的：比 CLI 自己清單裡最新那顆還新、這一版 CLI 還沒試過的模型。

    舊型號不必試——CLI 認得更新的，自然跑得動更舊的。CLI 清單還沒拿到時不試，
    否則每一顆都會被當成「比最新的新」。"""
    if not _cat.api or not _cat.cli or not cli_ver:
        return []
    known = {base_id(c.resolved) for c in _cat.cli if c.resolved}
    cutoff = max((a["created"] for a in _cat.api if a["id"] in known), default="")
    if not cutoff:
        return []
    fresh = {a["id"] for a in _cat.api if a["created"] > cutoff}
    return [m for m in catalog()
            if m.resolved in fresh and (_cat.probes.get(m.resolved) or {}).get("cli") != cli_ver]


async def probe_new() -> int:
    """把 `_probe_candidates` 各試跑一句話，記下這一版 CLI 跑不跑得動。回試了幾顆。

    跑不動時後端在任何推論之前就回 400，不花額度；跑得動就是一句「OK」。
    網路之類跟模型無關的失敗不記，下次再試。"""
    cli_ver = await asyncio.to_thread(_cli_version)
    todo = _probe_candidates(cli_ver)
    for m in todo:
        ok, requires = await _probe(m.value)
        if ok is None:
            continue
        _cat.probes[m.resolved] = {"cli": cli_ver, "ok": ok, "requires": requires,
                                   "at": time.time()}
        log.info("試跑 %s：%s", m.value, "可用" if ok else f"需要 CLI {requires or '更新'}")
    if todo:
        _save()
    return len(todo)


async def _probe(value: str) -> tuple[bool | None, str]:
    """用這個模型跑一句話。回 (能不能跑, 要求的 CLI 版本)；(None, "") 是說不準。"""
    from claude_agent_sdk import ClaudeSDKClient, ResultMessage

    from .mailbox import iter_messages
    from .meta import ONE_SHOT_TIMEOUT_SEC, one_shot_options

    result: ResultMessage | None = None

    async def run() -> None:
        nonlocal result
        async with ClaudeSDKClient(one_shot_options(value)) as c:
            await c.query("只回覆 OK")
            async for msg in iter_messages(c):
                if isinstance(msg, ResultMessage):
                    result = msg
                    break

    try:
        await asyncio.wait_for(run(), timeout=ONE_SHOT_TIMEOUT_SEC)
    except Exception:  # noqa: BLE001
        log.warning("試跑 %s 沒有結果", value, exc_info=True)
        return None, ""
    if result is None:
        return None, ""
    if not result.is_error:
        return True, ""
    return note_rejected(value, result.result or "")


def note_rejected(value: str, text: str) -> tuple[bool | None, str]:
    """回合失敗的文字若是「這版 CLI 不支援這個模型」，記下來並回 (False, 要求的版本)。

    runner 碰到這種錯誤也叫這裡：使用者真的選了、真的撞牆了，下次開面板就該灰掉，
    不必等試跑。其他錯誤回 (None, "")，不記。"""
    s = text or ""
    if "does not support this model" not in s.lower():
        return None, ""
    m = _REQUIRES_RE.search(s)
    requires = m.group(1) if m else ""
    info = find(value)
    if info is not None:
        _cat.probes[info.resolved] = {"cli": _cli_version(), "ok": False,
                                      "requires": requires, "at": time.time()}
        _save()
    return False, requires


async def bootstrap() -> bool:
    """啟動時：沒有 CLI 清單快取就自己開一條連線拿，拿完就關；官方清單過期就更新。

    不做的話，重啟到第一則訊息之間 `/v1/settings` 只有內建後備，App 那時開面板
    就少模型。有快取就不必付這次進程啟動。永不拋例外。"""
    ok = False
    await asyncio.to_thread(_cli_version)         # 先問好版本，catalog() 才標得出試跑結果
    if not _cat.cli:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
            c = ClaudeSDKClient(ClaudeAgentOptions(cwd=str(config.DEFAULT_CWD),
                                                   cli_path=config.CLAUDE_CLI))
            await c.connect()
            try:
                ok = await refresh_from(c)
            finally:
                await c.disconnect()
            log.info("啟動時向 CLI 拿模型清單：%s", "成功" if ok else "沒拿到")
        except Exception:  # noqa: BLE001
            log.warning("啟動時拿模型清單失敗，先用快取或內建後備", exc_info=True)
    schedule_refresh()
    await asyncio.gather(*_tasks, return_exceptions=True)
    return ok


def reset_for_tests() -> None:
    global _cat
    _cat = _Catalog()
    _tasks.clear()
