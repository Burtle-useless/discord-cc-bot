"""語意搜尋歷史對話：向量臂 ＋ 字面臂，用 RRF 融合。

這是 cc-bot 這個前端獨有的東西（引擎的 `engine.search` 只有關鍵字版）：
人回頭找一段舊對話時記得的是「那次在講什麼」，不是當初打過哪個字。

**fastembed 是選配。** 沒裝（或載入失敗、或沒有 numpy）就整個退回引擎的關鍵字搜尋，
呼叫端從回傳的 mode 知道走的是哪一條，畫面上講清楚。

e5 是**非對稱**檢索模型：文件要加 `passage: `、查詢要加 `query: `，這是訓練時就
定好的，不加前綴會明顯掉分。切塊 450 字是為了壓在 512 token 上限內——超過會被
靜默截斷，索引到一半的內容比沒索引更難查出問題。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

E5_MODEL = "intfloat/multilingual-e5-large"
INDEX_CHARS = 6000      # 每個 session 取前 N 字進索引
CHUNK_SIZE = 450        # 單塊字元數，壓在 e5 的 512 token 上限內
CHUNK_OVERLAP = 80      # 相鄰塊重疊，避免語意剛好被切在邊界
RRF_K = 60              # RRF 的平滑常數，60 是原論文的建議值

_model: Any = None
_unavailable = False
_cache_file: Path | None = None


def set_cache_file(path: Path) -> None:
    """向量快取檔的位置（由 bootstrap 指到 `data/session_vectors_e5.json`）。"""
    global _cache_file
    _cache_file = path


def _get_model() -> Any:
    """延遲載入 embedding 模型。載不起來就永久標記不可用，不要每次搜尋都重試一遍。"""
    global _model, _unavailable
    if _unavailable:
        return None
    if _model is not None:
        return _model
    try:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=E5_MODEL)
        return _model
    except Exception as e:  # noqa: BLE001
        _unavailable = True
        log.info("fastembed 不可用，/search 退回字面搜尋：%r", e)
        return None


def chunk(text: str) -> list[str]:
    """切成有重疊的塊。空字串回空清單。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    step = CHUNK_SIZE - CHUNK_OVERLAP
    out = []
    for i in range(0, len(text), step):
        out.append(text[i:i + CHUNK_SIZE])
        if i + CHUNK_SIZE >= len(text):
            break
    return out


def _load_cache() -> dict:
    if _cache_file is None or not _cache_file.exists():
        return {}
    try:
        data = json.loads(_cache_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — 快取壞了就重建，不是錯誤
        return {}


def _save_cache(cache: dict) -> None:
    if _cache_file is None:
        return
    try:
        tmp = _cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_cache_file)
    except Exception:  # noqa: BLE001
        log.warning("寫向量快取失敗")


def _index(sessions: dict[str, float]) -> dict:
    """增量建索引：只對新出現或改過的 session 重算，回整份快取。

    重算是批次做的——embedding 一次算一大批比逐段呼叫快很多，所以先把所有待算
    session 的塊攤平成一個清單，算完再依區間切回各 session。
    """
    model = _get_model()
    if model is None:
        return {}
    from engine.sessions import session_text
    cache = _load_cache()

    pending: list[tuple[str, float, list[str]]] = []
    for sid, mtime in sessions.items():
        cached = cache.get(sid)
        if cached and "vecs" in cached and abs(cached.get("mtime", 0.0) - mtime) < 1e-6:
            continue
        chunks = chunk(session_text(sid, INDEX_CHARS, "head"))
        if chunks:
            pending.append((sid, mtime, chunks))

    if pending:
        flat: list[str] = []
        spans: list[tuple[str, float, int, int]] = []
        for sid, mtime, chunks in pending:
            start = len(flat)
            flat.extend(f"passage: {c}" for c in chunks)
            spans.append((sid, mtime, start, len(flat)))
        vecs = list(model.embed(flat))
        for sid, mtime, s, e in spans:
            cache[sid] = {"mtime": mtime,
                          "vecs": [[float(x) for x in v] for v in vecs[s:e]]}

    gone = [sid for sid in cache if sid not in sessions]
    for sid in gone:
        del cache[sid]
    if pending or gone:
        _save_cache(cache)
    return cache


def search(query: str, limit: int = 25) -> tuple[list[dict], str]:
    """搜尋，回 `(結果, 模式)`；模式是 "semantic" 或 "literal"。

    結果每筆帶 session_id／title／cwd／mtime／snippet，形狀跟
    `engine.sessions.scan_sessions` 一致，接管流程可以直接吃。
    """
    files = _session_files()
    lit = literal(query, files, 50)
    hits = _semantic(query, lit, files, limit)
    if hits is not None:
        return hits, "semantic"
    return lit[:limit], "literal"


def _session_files() -> dict[str, Path]:
    """電腦上所有 session 逐字稿：session_id → 檔案路徑。

    刻意掃**全部**而不是只掃已登記的對話——`/search` 要能找回「那次在別的地方
    講過的東西」，找到之後走接管把它接成新頻道。`engine.search` 只搜已登記的，
    對這個用途不夠。
    """
    from engine.sessions import _projects_dir
    out: dict[str, Path] = {}
    root = _projects_dir()
    if not root.is_dir():
        return out
    for proj in root.iterdir():
        if not proj.is_dir():
            continue
        for jf in proj.glob("*.jsonl"):
            out[jf.stem] = jf
    return out


def literal(query: str, files: dict[str, Path], limit: int) -> list[dict]:
    """字面搜尋：逐字稿裡出現這個字串就算命中，取命中點前後當摘要。"""
    kw = (query or "").strip().lower()
    if not kw:
        return []
    from engine.sessions import _head_scan
    hits: list[dict] = []
    for sid, jf in files.items():
        snippet = ""
        try:
            with jf.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    low = line.lower()
                    pos = low.find(kw)
                    if pos < 0:
                        continue
                    s = max(0, pos - 40)
                    snippet = " ".join(line[s:pos + 80].split())[:140]
                    break
        except OSError:      # 掃到一半檔案被刪（CC 會清空殼 session）
            continue
        if not snippet:
            continue
        try:
            mtime = jf.stat().st_mtime
        except OSError:
            continue
        head = _head_scan(jf) or {}
        hits.append({
            "session_id": sid,
            "title": " ".join(str(head.get("text") or "").split())[:40] or sid[:8],
            "cwd": str(head.get("cwd") or ""),
            "mtime": mtime,
            "snippet": snippet,
        })
    hits.sort(key=lambda h: h["mtime"], reverse=True)
    return hits[:limit]


def _semantic(query: str, lit: list[dict], files: dict[str, Path],
              limit: int) -> list[dict] | None:
    """向量臂＋字面臂的 RRF 融合。任何一個前置條件不成立就回 None（退回字面）。"""
    model = _get_model()
    if model is None:
        return None
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return None

    sessions: dict[str, float] = {}
    for sid, jf in files.items():
        try:
            sessions[sid] = jf.stat().st_mtime
        except OSError:      # 掃到一半被刪掉（CC 自己會清空殼）
            continue
    cache = _index(sessions)
    if not cache:
        return None

    # 查詢向量與所有塊疊成矩陣，一次算完 cosine；每個 session 取「最像的那一塊」
    q = np.asarray(list(model.embed([f"query: {query}"]))[0], dtype=np.float32)
    q /= (float(np.linalg.norm(q)) + 1e-9)
    flat: list[list[float]] = []
    spans: list[tuple[str, int, int]] = []
    for sid, rec in cache.items():
        vecs = rec.get("vecs") or []
        if vecs:
            spans.append((sid, len(flat), len(flat) + len(vecs)))
            flat.extend(vecs)
    if not flat:
        return None
    mat = np.asarray(flat, dtype=np.float32)
    mat /= (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    sims = mat @ q
    scored = sorted(((float(sims[s:e].max()), sid) for sid, s, e in spans), reverse=True)

    # RRF：兩臂各給 1/(K+名次)，兩邊都上榜的自然被抬起來。語意抓相關、
    # 字面補精準（人名、指令名、錯誤碼這種向量常漏的）
    fused: dict[str, float] = {}
    for i, (_s, sid) in enumerate(scored):
        fused[sid] = fused.get(sid, 0.0) + 1.0 / (RRF_K + i)
    lit_by_sid = {e["session_id"]: e for e in lit}
    for i, e in enumerate(lit):
        sid = e["session_id"]
        fused[sid] = fused.get(sid, 0.0) + 1.0 / (RRF_K + i)

    from engine.sessions import _head_scan
    from engine.state import get_title, _load_map
    conv_by_sid = {(r or {}).get("session_id"): c for c, r in (_load_map() or {}).items()}
    out: list[dict] = []
    for sid in sorted(fused, key=lambda s: fused[s], reverse=True):
        jf = files.get(sid)
        if jf is None:
            continue
        head = _head_scan(jf) or {}
        conv = conv_by_sid.get(sid)
        snippet = (lit_by_sid.get(sid, {}).get("snippet")
                   or " ".join(str(head.get("text") or "").split())[:120])
        out.append({
            "session_id": sid,
            "title": (get_title(conv) if conv else None) or snippet[:40] or sid[:8],
            "cwd": str(head.get("cwd") or ""),
            "mtime": sessions.get(sid, 0.0),
            "snippet": snippet,
        })
        if len(out) >= limit:
            break
    return out
