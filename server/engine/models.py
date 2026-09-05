"""官方模型清單：從 CLI 拿、快取、給設定頁與驗證用。

模型選項要跟著官方走（使用者 2026-09-02 明確要求）。CLI 的 initialize 回應本來就帶
`models`（實測 `_diag/server_info_probe.py`）：

    {"value": "default", "resolvedModel": "claude-opus-5[1m]",
     "displayName": "Default (recommended)",
     "description": "Opus 5 with 1M context · Best for everyday, complex tasks",
     "supportsEffort": true,
     "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"], ...}

`value` 是官方 app 選單用的別名（`default`／`opus[1m]`／`sonnet`／`haiku`／
`claude-fable-5[1m]`），直接當 `ClaudeAgentOptions.model` 都能跑（`_diag/model_alias_probe.py`
六個值全過），CLI 升版清單就自己長出新模型。先前 `transport/app.py` 寫死六個完整 id，
每次官方換代都要人工追。

**快取落檔**：清單要在第一個 client 連上之後才拿得到，但設定頁在那之前就可能打開，
所以每次拿到就寫進 `data/models.json`，啟動時先讀它頂著。沒有快取又還沒連過線時
退回一份最小的內建清單，只保證設定頁不是空的。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import config
from util import atomic_write_text, read_text_with_retry

log = logging.getLogger(__name__)

CATALOG_FILE = config.DATA_DIR / "models.json"

# 完整的思考等級清單，給沒標 supportsEffort 的模型（例如 haiku）當後備：
# 送給 CLI 不會出錯，只是它會忽略。
ALL_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# 一次性 meta 查詢（生標題）用的小模型。這是全庫唯一寫死完整 id 的地方：
# 標題生成走的是自己建、用完就丟的 client，不經過設定頁，也不該跟著帳號預設走
# （預設換成 Opus 就是拿 Opus 生 20 字標題）。換代時只改這裡。
META_MODEL: str = "claude-haiku-4-5-20251001"

# 多久重新向 CLI 要一次。清單只在 CLI 升版時變，每個 client 連線都問一次是浪費，
# 但一小時問一次的成本是一個控制通道往返，可以忽略。
REFRESH_INTERVAL_SEC = 3600.0


@dataclass(frozen=True)
class ModelInfo:
    value: str                       # 給 options.model 用的值（官方別名或完整 id）
    resolved: str                    # 實際會用的模型 id
    name: str                        # 顯示名（Default (recommended) / Opus (1M context) / …）
    description: str = ""
    efforts: tuple[str, ...] = ALL_EFFORTS
    supports_effort: bool = True

    def to_wire(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "resolved": self.resolved,
            "name": self.name,
            "description": self.description,
            "efforts": list(self.efforts),
            "supports_effort": self.supports_effort,
        }


# 還沒連過線、也沒有快取時的後備。只列別名，讓 CLI 自己解析——寫死完整 id 才是
# 之前那個「官方換代就過期」的病。
_FALLBACK: tuple[ModelInfo, ...] = (
    ModelInfo("default", "", "Default (recommended)", "跟著 Claude Code 的預設走"),
    ModelInfo("opus", "", "Opus", ""),
    # Fable 沒有短別名，只能寫這個值（2026-09-03 官方清單就是這個字串）。使用者
    # 最常切的正是它，後備清單少了它比多一個會過期的 id 更糟——2026-09-03 重啟後
    # 第一次連線前手機面板就是少了 Fable
    ModelInfo("claude-fable-5[1m]", "", "Fable", ""),
    ModelInfo("sonnet", "", "Sonnet", ""),
    ModelInfo("haiku", "", "Haiku", "", efforts=(), supports_effort=False),
)


# CLI 清單落後官方發佈時的補充：後端已認得、initialize 清單還沒列的模型。
# 退場不用人工——比對是照 resolved id 做的，CLI 哪天收錄了同一顆，這裡那筆就
# 自動不再出現。**進場前要實測**：直接拿 id 開一個 client 跑一句話，能跑才加，
# 不然面板上會多一顆選了就炸的模型。
_KNOWN_EXTRA: tuple[ModelInfo, ...] = (
    # Fable 5.1（官方 2026-09-01 發佈）。CLI 2.1.259 的清單仍只有 Fable 5，
    # 但 claude-fable-5-1 與 [1m] 別名實測都能跑（scratchpad/fable51_probe）。
    ModelInfo("claude-fable-5-1[1m]", "claude-fable-5-1", "Fable 5.1",
              "Fable 5.1 · For demanding reasoning and long-horizon agentic work"),
)


@dataclass
class _Catalog:
    models: list[ModelInfo] = field(default_factory=list)
    fetched_at: float = 0.0          # 最後一次真的向 CLI 拿到的時刻（epoch）
    source: str = "fallback"         # fallback / cache / cli


_cat = _Catalog()


def _parse(raw: Any) -> list[ModelInfo]:
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


def _load_cache() -> None:
    if not CATALOG_FILE.exists():
        return
    try:
        data = json.loads(read_text_with_retry(CATALOG_FILE))
        models = _parse(data.get("models"))
    except Exception:  # noqa: BLE001 — 快取壞了就當沒有，下次連線會重寫
        log.warning("models.json 讀不出來，先用內建清單")
        return
    if models:
        _cat.models = models
        _cat.fetched_at = float(data.get("fetched_at") or 0.0)
        _cat.source = "cache"


_load_cache()


def catalog() -> list[ModelInfo]:
    """目前知道的模型清單（CLI → 快取 → 內建後備），再補上清單還沒跟上的新模型。

    補充插在同家族的正後面（resolved 的共同字首），沒有同家族才排最後——
    Fable 5.1 要出現在 Fable 旁邊，不是壓在清單最底下。
    """
    base = list(_cat.models) if _cat.models else list(_FALLBACK)
    known = {m.value for m in base} | {m.resolved for m in base if m.resolved}
    for extra in _KNOWN_EXTRA:
        if extra.value in known or extra.resolved in known:
            continue
        stem = extra.resolved.rsplit("-", 2)[0]      # claude-fable-5-1 → claude-fable
        at = next((i + 1 for i in reversed(range(len(base)))
                   if base[i].resolved.startswith(stem) or base[i].value.startswith(stem)),
                  len(base))
        base.insert(at, extra)
    return base


def values() -> list[str]:
    return [m.value for m in catalog()]


def find(value: str | None) -> ModelInfo | None:
    if not value:
        return None
    for m in catalog():
        if m.value == value or (m.resolved and m.resolved == value):
            return m
    return None


def is_known(value: str) -> bool:
    """設定端點用的驗證：清單裡的值、清單裡的完整 id，或任何 claude- 開頭的 id
    （舊資料存的是完整 id；CLI 自己會擋真的不存在的模型）。"""
    return find(value) is not None or value.startswith("claude-")


def resolve(value: str) -> str:
    """把別名換成實際的模型 id（記帳用）；認不得的原樣回。"""
    m = find(value)
    return m.resolved or value if m else value


def efforts_for(value: str | None) -> list[str]:
    """這個模型能選的思考等級。清單裡標了就照標的，沒標一律給完整清單。"""
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


async def refresh_from(client: Any) -> bool:
    """向一個已連線的 client 要一次清單。有拿到就更新記憶體＋快取檔，回 True。

    呼叫端（client_pool）在每次建立連線後叫；這裡自己控制頻率，
    一小時內拿過就直接回 False，不打控制通道。永不拋例外——拿不到清單不能
    讓對話回合失敗。
    """
    if not needs_refresh():
        return False
    try:
        info = await client.get_server_info()
        models = _parse((info or {}).get("models"))
    except Exception:  # noqa: BLE001
        log.warning("向 CLI 要模型清單失敗，沿用現有的", exc_info=True)
        return False
    if not models:
        return False
    _cat.models = models
    _cat.fetched_at = time.time()
    _cat.source = "cli"
    try:
        atomic_write_text(CATALOG_FILE, json.dumps({
            "fetched_at": _cat.fetched_at,
            "models": [
                {
                    "value": m.value, "resolvedModel": m.resolved, "displayName": m.name,
                    "description": m.description, "supportsEffort": m.supports_effort,
                    "supportedEffortLevels": list(m.efforts),
                }
                for m in models
            ],
        }, ensure_ascii=False, indent=1))
    except Exception:  # noqa: BLE001
        log.warning("models.json 寫入失敗", exc_info=True)
    return True


async def bootstrap() -> bool:
    """啟動時沒有快取就自己開一條連線拿清單，拿完就關。

    不做的話，重啟到第一則訊息之間 `/v1/settings` 只有內建後備，App 那時開面板
    就少模型；而 App 只在開啟時抓一次設定，之後一直沿用（2026-09-03 實錄）。
    有快取（source 是 cache／cli）就不必付這次進程啟動。永不拋例外。
    """
    if _cat.source != "fallback":
        return False
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        c = ClaudeSDKClient(ClaudeAgentOptions(cwd=str(config.DEFAULT_CWD)))
        await c.connect()
        try:
            ok = await refresh_from(c)
        finally:
            await c.disconnect()
        log.info("啟動時向 CLI 拿模型清單：%s", "成功" if ok else "沒拿到")
        return ok
    except Exception:  # noqa: BLE001
        log.warning("啟動時拿模型清單失敗，先用內建後備", exc_info=True)
        return False


def reset_for_tests() -> None:
    global _cat
    _cat = _Catalog()
