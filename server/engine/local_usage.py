"""本機用量：這台電腦上**所有** Claude Code session 的 token 明細。

跟另外兩支的分工（三件事，不要混）：
  usage.py      ── 走助理這條路的回合，butler 自己記的帳
  plan_usage.py ── 訂閱方案還剩多少額度，全帳號，含網頁版與手機 App
  local_usage.py ── 這台電腦上所有 CC 的 token，含 cc-bot 與終端機直接開的

plan_usage 給的是「總量剩多少」，回答不了「被誰吃掉的」。這支補的就是組成：
資料來源是 ~/.claude/projects 下的逐字稿，每則回應都帶著 usage 欄位。

拿不到金額。成本只在 SDK 的 ResultMessage 裡，那則不會寫進逐字稿。
訂閱制下金額本來也沒有意義（見 plan_usage 的說明），token 才是會撞上限的東西。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from util import read_text_with_retry, replace_with_retry

PROJECTS_DIR: Path = config.claude_projects_dir()     # 模組變數是給測試換目錄用
STATE_FILE: Path = config.DATA_DIR / "local_usage.json"

# 留多久。跟 usage.py 對齊，手機上兩者要畫在同一張圖裡。
KEEP_DAYS = 92

# 掃描間隔。逐字稿只在有人用 CC 時才長大，一分鐘一次已經比任何人的閱讀頻率快。
SCAN_EVERY_SEC = 60

# 統計口徑版本。改了切法（新增維度、換去重規則）就加一，舊的累計值會被丟掉重掃。
# v3：files 開始記每個檔案的逐日貢獻，逐字稿被改寫時才扣得掉（見 scan 的說明）。
# 這一版順便把 v2 期間被重複計數污染的累計值一起丟掉重算。
VERSION = 3

_LOCK = threading.Lock()
_scanning = False       # 掃描是否正在進行，避免同時跑兩份


def _empty() -> dict[str, Any]:
    return {"v": VERSION, "days": {}, "files": {}, "scanned_at": "", "elapsed": 0.0}


def _load() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return _empty()
    try:
        data = json.loads(read_text_with_retry(STATE_FILE))
    except (OSError, json.JSONDecodeError):
        return _empty()          # 壞檔就重掃，這份資料隨時可以從逐字稿重建
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        return _empty()
    # 統計口徑改過就整份重來。舊資料留著會跟新口徑疊加，圖表上是憑空多出來的量，
    # 而且看不出哪裡錯——重掃只要三秒，不值得為它寫一套遷移。
    if data.get("v") != VERSION:
        return _empty()
    data.setdefault("files", {})
    return data


def _save(data: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    replace_with_retry(tmp, STATE_FILE)


def _bucket() -> dict[str, Any]:
    return {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "turns": 0}


def _add(dst: dict[str, Any], src: dict[str, int]) -> None:
    for k in ("in", "out", "cache_read", "cache_write", "turns"):
        dst[k] = int(dst.get(k, 0)) + int(src.get(k, 0))


def _sub(dst: dict[str, Any], src: dict[str, int]) -> None:
    """扣掉一筆先前加過的量。夾在零以上：帳寧可少算也不要出現負數，
    那會讓圖表的長條倒著畫，而且看不出來是哪個檔案扣過頭。"""
    for k in ("in", "out", "cache_read", "cache_write", "turns"):
        dst[k] = max(0, int(dst.get(k, 0)) - int(src.get(k, 0)))


def _unapply(days: dict[str, Any], contrib: dict[str, Any],
             project: str, kind: str) -> None:
    """把某個檔案先前記過的量從總帳扣掉。

    `contrib` 的形狀是 `{日期: {模型: 用量}}`。專案與主／子代理對整份檔案
    是固定的，所以由呼叫端傳進來，不必存進 contrib 裡佔空間。
    """
    for day, by_model in contrib.items():
        bucket = days.get(day)
        if not isinstance(bucket, dict):
            continue
        for model, one in by_model.items():
            _sub(bucket, one)
            for dim, name in (("projects", project), ("models", model), ("kinds", kind)):
                sub = (bucket.get(dim) or {}).get(name)
                if isinstance(sub, dict):
                    _sub(sub, one)


def _local_date(ts: str) -> str:
    """逐字稿的 timestamp 是 UTC，要換算成本地日期才對得上 usage.py 的記帳。

    差八小時的意思是：半夜十二點前後的用量會被記到前一天，圖表上兩條線對不齊。
    """
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().date().isoformat()
    except (ValueError, AttributeError):
        return ""


def _scan_file(path: Path, start: int) -> tuple[list[tuple[str, str, dict]], int]:
    """從 byte offset 讀到檔尾，回傳 (筆數清單, 新的 offset)。

    每筆是 (日期, 模型, 用量)。專案與主／子代理由呼叫端從路徑決定——
    那兩個對整份檔案是固定的，逐行重算沒有意義。

    **一定要用二進位模式並自己累加位移。** 先前這裡是文字模式 `for line in f`
    配 `f.tell()`，而 Python 的 TextIOWrapper 只要被 `__next__` 迭代過就會禁用
    tell：中途 `break` 之後呼叫它一律拋
    `OSError: telling position disabled by next() call`。
    那個例外被下面的 `except OSError` 接住，於是整批已經讀好的資料連同位移一起
    丟掉——也就是說**只要逐字稿的最後一行還沒寫完（CC 正在跑），這個檔案就完全
    計不到帳**，要等它閒下來、最後一行補上換行符才補算。
    2026-08-17 實測確認。二進位模式沒有這個限制，位移也精確。
    """
    rows: list[tuple[str, str, dict]] = []
    last_req = ""
    pos = start
    try:
        with path.open("rb") as f:
            f.seek(start)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break            # 最後一行還沒寫完，留到下次（pos 不前進）
                pos += len(raw)
                try:
                    d = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                u = msg.get("usage") or {}
                if not u:
                    continue
                # 同一個 API request 的多個 assistant 行帶著**完全相同**的 usage
                # （一行文字一行工具呼叫是常態）。不去重的話用量會憑空翻倍。
                req = str(d.get("requestId") or msg.get("id") or "")
                if req and req == last_req:
                    continue
                last_req = req
                day = _local_date(str(d.get("timestamp") or ""))
                if not day:
                    continue
                rows.append((day, str(msg.get("model") or "unknown"), {
                    "in": int(u.get("input_tokens") or 0),
                    "out": int(u.get("output_tokens") or 0),
                    "cache_read": int(u.get("cache_read_input_tokens") or 0),
                    "cache_write": int(u.get("cache_creation_input_tokens") or 0),
                    "turns": 1,
                }))
            return rows, pos
    except OSError:
        return [], start


def scan() -> dict[str, Any]:
    """增量掃描所有逐字稿。回傳掃完的狀態。

    只讀每個檔案新長出來的那一段。唯一會整份重讀的情況是檔案**變小**——
    CC 的 auto-compact 會就地改寫逐字稿，舊內容被摘要取代，這時沿用舊的
    offset 會從一個對不上的位置開始切，讀出來的是半行 JSON。

    重讀前一定要先把這個檔案先前記過的量扣掉。`days` 是全域累加的桶，
    先前只把 offset 歸零就重掃，同一段對話於是被算第二次——auto-compact 每
    發生一次，該檔涵蓋的所有日期就再加一輪，今日與本月的 token 憑空翻倍，
    而且錯誤永久留在 local_usage.json 裡。所以 `files[key]` 除了 offset
    還記著這個檔案的逐日貢獻，扣得掉才敢重掃。
    """
    global _scanning
    with _LOCK:
        if _scanning:
            return _load()
        _scanning = True
    started = time.time()
    try:
        data = _load()
        days: dict[str, Any] = data["days"]
        files: dict[str, Any] = data["files"]
        # 遞迴：subagent 與 workflow 的逐字稿埋在 <session>/subagents/ 底下，
        # 只掃一層會漏掉 320 個檔——那些是真的花掉的 token，不是附屬紀錄。
        for path in sorted(PROJECTS_DIR.glob("**/*.jsonl")):
            try:
                rel = path.relative_to(PROJECTS_DIR)
            except ValueError:
                continue
            key = rel.as_posix()
            project = rel.parts[0]
            kind = "subagent" if "subagents" in rel.parts else "main"
            try:
                size = path.stat().st_size
            except OSError:
                continue
            prev = files.get(key) or {}
            start = int(prev.get("offset", 0))
            if size == start:
                continue                 # 沒長大，跳過
            # 這個檔案先前貢獻過的量。重掃時要先還原，累加時要繼續記。
            contrib: dict[str, Any] = prev.get("days") or {}
            if size < start:
                _unapply(days, contrib, project, kind)
                contrib = {}
                start = 0                # 被改寫過，整份重來
            rows, end = _scan_file(path, start)
            for day, model, one in rows:
                bucket = days.setdefault(
                    day, {"projects": {}, "models": {}, "kinds": {}, **_bucket()},
                )
                for dim in ("projects", "models", "kinds"):
                    bucket.setdefault(dim, {})
                _add(bucket, one)
                _add(bucket["projects"].setdefault(project, _bucket()), one)
                _add(bucket["models"].setdefault(model, _bucket()), one)
                _add(bucket["kinds"].setdefault(kind, _bucket()), one)
                _add(contrib.setdefault(day, {}).setdefault(model, _bucket()), one)
            files[key] = {"offset": end, "days": contrib}
        for k in sorted(days)[: max(0, len(days) - KEEP_DAYS)]:
            days.pop(k, None)
        # 存到秒。先前只存到分，而 _fresh_enough 是拿它跟 60 秒比——
        # 掃描發生在第 59 秒時，過一秒就跨分鐘、判定過期，實際間隔在 1～60 秒之間
        # 隨機亂跳。每次判定過期都要重跑一次全目錄 glob 加 stat，在 1.2GB 的
        # 逐字稿上並不便宜。多存 3 個字元就換掉這個隨機性。
        # 舊資料是分鐘精度，fromisoformat 照樣解析得動，不必轉檔。
        data["scanned_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        data["elapsed"] = round(time.time() - started, 1)
        _save(data)
        return data
    finally:
        _scanning = False


def _fresh_enough(data: dict[str, Any]) -> bool:
    try:
        at = datetime.fromisoformat(data.get("scanned_at") or "")
    except ValueError:
        return False
    return (datetime.now() - at).total_seconds() < SCAN_EVERY_SEC


def report(span: int = 14) -> dict[str, Any]:
    """本機用量報表：今日、本月、逐日、本月按專案與模型的分佈。

    資料太舊才重掃。1.2GB 的逐字稿首掃要跑十幾秒，掛在 HTTP 請求上會逾時，
    所以呼叫端該用背景執行緒叫它（見 transport/app.py 的 /v1/usage）。
    """
    data = _load()
    if not _fresh_enough(data):
        data = scan()
    days: dict[str, Any] = data["days"]

    today = datetime.now().date()
    today_key = today.isoformat()
    month_key = today.strftime("%Y-%m")

    month = _bucket()
    projects: dict[str, dict[str, Any]] = {}
    models: dict[str, dict[str, Any]] = {}
    kinds: dict[str, dict[str, Any]] = {}
    for key, row in days.items():
        if not key.startswith(month_key):
            continue
        _add(month, row)
        for dim, into in (("projects", projects), ("models", models), ("kinds", kinds)):
            for name, v in (row.get(dim) or {}).items():
                _add(into.setdefault(name, _bucket()), v)

    ordinal = today.toordinal()
    series = []
    for i in range(span - 1, -1, -1):
        key = datetime.fromordinal(ordinal - i).date().isoformat()
        row = days.get(key) or _bucket()
        series.append({
            "date": key,
            "in": int(row.get("in", 0)),
            "out": int(row.get("out", 0)),
            "cache_read": int(row.get("cache_read", 0)),
            "turns": int(row.get("turns", 0)),
        })

    def _rank(d: dict[str, dict[str, Any]], label: str) -> list[dict]:
        # 按「真的會算進額度的量」排：cache_read 動輒是 input 的百倍，
        # 拿總和排序的話每一列都會被快取讀取洗成同一個名次。
        return sorted(
            ({label: n, **v} for n, v in d.items()),
            key=lambda r: r["in"] + r["out"], reverse=True,
        )

    return {
        "today": {**_bucket(), **{k: v for k, v in (days.get(today_key) or {}).items()
                                  if k not in ("projects", "models", "kinds")}},
        "month": month,
        "month_key": month_key,
        "days": series,
        "projects": _rank(projects, "project"),
        "models": _rank(models, "model"),
        "kinds": _rank(kinds, "kind"),
        "scanned_at": data.get("scanned_at", ""),
        "scan_sec": data.get("elapsed", 0.0),
    }
