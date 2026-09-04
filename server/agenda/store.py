"""行事曆、鬧鐘、記帳、課表的資料層。

單一職責：只管「讀出來、改回去、寫到磁碟」。不碰 HTTP，也不碰 CC 工具——
上面兩層（transport/agenda_api.py 與 engine/agenda_tools.py）都呼叫這裡，
不各自寫一套，否則助理改的跟你手動改的會走出兩種行為。

四類東西存同一個檔而不是四個：它們一起被讀（App 打開日常頁就要全部）、
量都很小（幾百筆頂天），拆開只是多幾次 IO 跟幾份鎖。

**時間一律是本機時間的裸字串**，不帶時區。這台電腦跟這支手機都在同一個時區，
帶 tz 只會讓「早上七點」在序列化來回之間變成七點零一分或差八小時。
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import config
from util import read_text_with_retry, replace_with_retry

AGENDA_FILE = config.DATA_DIR / "agenda.json"

# 讀-改-寫不是原子操作，而 HTTP 路由與 CC 工具可能同時進來（都經 to_thread，
# 是真的多執行緒）。鎖住整個檔案，反正單次操作是微秒級。
_LOCK = threading.Lock()

Kind = Literal["events", "alarms", "ledger", "courses"]
_KINDS: tuple[Kind, ...] = ("events", "alarms", "ledger", "courses")

# 節次時間表不是 _KINDS 的一員：那四類是「一筆一筆的東西」，有 id、可增可刪；
# 節次表是一份**固定長度的對照表**（第幾節＝幾點到幾點），只會被整份改寫。
# 硬塞進同一套 add/update/remove 只會逼呼叫端去猜哪個 id 是第三節。
PERIODS_KEY = "periods"

# 每個學校的節次時間都不一樣，這裡只是**預設值**，讓課表一開始就有東西可用；
# 使用者在 App 上改過就以他的為準（見 set_periods）。
# 這組是台灣多數大學的常見排法：整點過十分上課、每節五十分鐘、中午空一節。
DEFAULT_PERIODS: tuple[dict[str, Any], ...] = (
    {"no": 1, "start": "08:10", "end": "09:00"},
    {"no": 2, "start": "09:10", "end": "10:00"},
    {"no": 3, "start": "10:10", "end": "11:00"},
    {"no": 4, "start": "11:10", "end": "12:00"},
    {"no": 5, "start": "13:10", "end": "14:00"},
    {"no": 6, "start": "14:10", "end": "15:00"},
    {"no": 7, "start": "15:10", "end": "16:00"},
    {"no": 8, "start": "16:10", "end": "17:00"},
    {"no": 9, "start": "17:10", "end": "18:00"},
    {"no": 10, "start": "18:30", "end": "19:20"},
    {"no": 11, "start": "19:25", "end": "20:15"},
    {"no": 12, "start": "20:20", "end": "21:10"},
)

# 一天最多幾節。上限存在的理由是擋掉「第 300 節」這種明顯的手滑，
# 不是為了對齊 DEFAULT_PERIODS 的長度——節次表可以被改成更多或更少節。
MAX_PERIOD = 16

# 記帳分類。固定清單而不是自由文字：分類要能拿來加總，
# 放任自由輸入的話「餐飲」「吃飯」「伙食」會變成三個獨立分類，月結就沒意義了。
CATEGORIES: tuple[str, ...] = (
    "餐飲", "交通", "日用", "娛樂", "學習", "醫療", "人情", "其他",
)

_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class AgendaError(ValueError):
    """輸入不合法。訊息會直接給助理看，所以要寫成它讀得懂的中文。"""


def _empty() -> dict[str, list[dict[str, Any]]]:
    return {**{k: [] for k in _KINDS}, PERIODS_KEY: [dict(p) for p in DEFAULT_PERIODS]}


def _quarantine(path: Path, why: str) -> None:
    """把讀不出來的檔案改名保留，不要讓它留在原位等著被覆蓋。

    「回空的，下次寫入會蓋過去」曾經寫在下面當理由，但那句話的後半才是問題：
    只要接著新增任何一筆行程，這份空結構就被整份存回去，原檔連同裡面的行程、
    鬧鐘、記帳、課表一起消失。而壞掉的通常只是尾端幾個 byte（寫到一半斷電、
    磁碟錯誤），前面的內容手動救得回來——前提是檔案還在。
    """
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path.rename(path.with_name(f"{path.name}.corrupt-{stamp}"))
        print(f"[store] {path.name} 讀取失敗（{why}），已改名保留，改用空資料啟動")
    except OSError as e:
        # 連改名都失敗就別再往下走了，這時候寫入只會讓情況更糟
        print(f"[store] {path.name} 壞檔隔離失敗：{e}")


def _load() -> dict[str, list[dict[str, Any]]]:
    if not AGENDA_FILE.exists():
        return _empty()
    # OSError 刻意**不接**。讀不到檔案（多半是 Windows 上有人正在 replace 換檔，
    # 見 util.read_text_with_retry）跟「檔案壞了」是兩回事，而這裡回空的代價極高：
    # 下一次寫入就把空結構整份存回去，行程、鬧鐘、記帳、課表一起沒了。
    # 讓它往上拋，這筆操作失敗、檔案原封不動——重試 20 次之後還讀不到，那是真的有事。
    try:
        raw = json.loads(read_text_with_retry(AGENDA_FILE))
    except json.JSONDecodeError:
        # 讀得到但不是合法 JSON——這才是壞檔。先隔離再回空的：服務照常起來，
        # 壞掉的通常只是尾端幾個 byte，前面的內容手動救得回來，前提是檔案還在。
        _quarantine(AGENDA_FILE, "JSON 解析失敗")
        return _empty()
    if not isinstance(raw, dict):
        # 解析得出來但根本不是我們的結構（例如被寫成一個陣列）。
        # 底下的 raw.get 會直接 AttributeError，一樣當壞檔處理。
        _quarantine(AGENDA_FILE, "根結構不是物件")
        return _empty()
    # 每個分類都必須是陣列。被手改成物件的話 `list()` 會安靜地得到一串 key 字串，
    # 真正爆掉的地方在很遠的 `row["id"]`——TypeError 直接變 500，而錯誤訊息
    # 完全指不回這個檔案。跟上面「根結構不是物件」同一類，一樣當壞檔隔離。
    # None 仍然放行（舊檔可能整個 key 是 null），那個下面的 `or []` 接得住。
    bad = [k for k in (*_KINDS, PERIODS_KEY)
           if raw.get(k) is not None and not isinstance(raw[k], list)]
    if bad:
        _quarantine(AGENDA_FILE, f"分類欄位不是陣列：{'、'.join(bad)}")
        return _empty()
    data: dict[str, Any] = {k: list(raw.get(k) or []) for k in _KINDS}
    # 舊的 agenda.json 沒有 periods（課表是後來才加的）。補預設而不是留空：
    # 空的節次表會讓課表畫面一片空白，而使用者根本不知道少了什麼。
    data[PERIODS_KEY] = list(raw.get(PERIODS_KEY) or [dict(p) for p in DEFAULT_PERIODS])
    return data


def _save(data: dict[str, list[dict[str, Any]]]) -> None:
    """先寫暫存檔再 replace：寫到一半斷電不會留下半個 JSON。"""
    AGENDA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AGENDA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 不用 tmp.replace：Windows 上目標檔正被別人開著讀時會拋 PermissionError
    # （見 util.replace_with_retry 的說明）。這裡失敗就等於整筆行程沒存進去。
    replace_with_retry(tmp, AGENDA_FILE)


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


# ── 欄位驗證 ─────────────────────────────────────────────────────────────────
# 助理是語言模型，它會傳「明天早上七點」這種東西進來。與其在工具層寫一套自然語言
# 解析（一定會錯，而且錯得沒有徵兆），不如在這裡硬性要求格式、錯了就把正確格式
# 回給它——模型看到錯誤訊息會自己改，這比我猜它的意思可靠。

def _need_datetime(v: Any, field: str) -> str:
    s = str(v or "").strip().replace(" ", "T")[:16]
    if not _DATETIME_RE.match(s):
        raise AgendaError(f"{field} 要寫成 YYYY-MM-DDTHH:MM，收到的是：{v!r}")
    try:
        datetime.strptime(s, "%Y-%m-%dT%H:%M")
    except ValueError as e:
        raise AgendaError(f"{field} 不是有效的時間：{v!r}（{e}）") from e
    return s


def _need_time(v: Any, field: str) -> str:
    s = str(v or "").strip()
    if len(s) == 4 and s[1] == ":":       # "7:30" 補成 "07:30"，這種錯太常見
        s = "0" + s
    if not _TIME_RE.match(s) or not (0 <= int(s[:2]) <= 23 and 0 <= int(s[3:]) <= 59):
        raise AgendaError(f"{field} 要寫成 24 小時制的 HH:MM，收到的是：{v!r}")
    return s


def _opt_date(v: Any, field: str) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    if not _DATE_RE.match(s):
        raise AgendaError(f"{field} 要寫成 YYYY-MM-DD，收到的是：{v!r}")
    return s


def _need_days(v: Any) -> list[int]:
    """重複日：0=週一 … 6=週日。空清單代表只響一次。"""
    if v in (None, "", []):
        return []
    if not isinstance(v, (list, tuple)):
        raise AgendaError("days 要給一個陣列，例如 [0,1,2,3,4] 代表平日")
    out: list[int] = []
    for d in v:
        try:
            n = int(d)
        except (TypeError, ValueError) as e:
            raise AgendaError(f"days 裡有非數字：{d!r}") from e
        if not 0 <= n <= 6:
            raise AgendaError(f"days 只能是 0 到 6（0=週一，6=週日），收到 {n}")
        if n not in out:
            out.append(n)
    return sorted(out)


def _need_day(v: Any) -> int:
    """星期。**0=週一 6=週日**，跟鬧鐘的 days 同一套編號，別再發明第二種。"""
    try:
        n = int(v)
    except (TypeError, ValueError) as e:
        raise AgendaError(f"day 要是 0 到 6 的數字（0=週一，6=週日），收到 {v!r}") from e
    if not 0 <= n <= 6:
        raise AgendaError(f"day 只能是 0 到 6（0=週一，6=週日），收到 {n}")
    return n


def _need_int(v: Any, field: str) -> int:
    """整數欄位。**不要在呼叫端直接 `int()`**——那拋的是 ValueError，
    而 HTTP 層的 `_guard` 與 MCP 工具的 except 都只認得 AgendaError，
    於是 `{"remind_min": "稍後"}` 變成 500 而不是 400，助理也拿不到能自我修正的中文訊息。
    """
    try:
        return int(v)
    except (TypeError, ValueError) as e:
        raise AgendaError(f"{field} 要是數字，收到 {v!r}") from e


def _need_amount(v: Any) -> float:
    """金額。正數檢查也在這裡，`update` 才不會繞過去（見 [_check_row] 的說明）。"""
    try:
        amt = round(float(v), 2)
    except (TypeError, ValueError) as e:
        raise AgendaError(f"amount 要是數字，收到 {v!r}") from e
    if amt <= 0:
        raise AgendaError("amount 要給正數；記收入請把 income 設為 true，不要用負數")
    return amt


def _need_period(v: Any, field: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError) as e:
        raise AgendaError(f"{field} 要是節次數字，收到 {v!r}") from e
    if not 1 <= n <= MAX_PERIOD:
        raise AgendaError(f"{field} 只能是 1 到 {MAX_PERIOD}，收到 {n}")
    return n


# ── 行事曆 ───────────────────────────────────────────────────────────────────
def add_event(
    title: str,
    start: str,
    end: str | None = None,
    note: str = "",
    remind_min: int = 10,
) -> dict[str, Any]:
    """新增一件行程。remind_min 是提前幾分鐘提醒，0 代表不提醒。"""
    if not str(title).strip():
        raise AgendaError("行程要有標題")
    item = {
        "id": _new_id("e"),
        "title": str(title).strip()[:100],
        "start": _need_datetime(start, "start"),
        "end": _need_datetime(end, "end") if end else None,
        "note": str(note or "").strip()[:500],
        "remind_min": max(0, _need_int(remind_min, "remind_min")),
        "done": False,
    }
    with _LOCK:
        data = _load()
        data["events"].append(item)
        data["events"].sort(key=lambda e: e["start"])
        _save(data)
    return item


# ── 鬧鐘 ─────────────────────────────────────────────────────────────────────
def _next_date_for(hhmm: str) -> str:
    """今天的這個時間還沒到就是今天，過了就是明天。"""
    from datetime import timedelta
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if hhmm > now.strftime("%H:%M"):
        return today
    return (now + timedelta(days=1)).strftime("%Y-%m-%d")


def add_alarm(
    time: str,
    label: str = "",
    days: list[int] | None = None,
    on_date: str | None = None,
) -> dict[str, Any]:
    """新增鬧鐘。

    days 有值就是每週重複；都沒給就是「下一個這個時間」響一次
    （on_date 明確指定哪一天時以它為準）。
    """
    hhmm = _need_time(time, "time")
    week = _need_days(days)
    when = _opt_date(on_date, "on_date")
    # 一次性鬧鐘一定要有明確日期。留 null 的話手機端只能理解成「下一次到這個時間」，
    # 而它在響完之後重排時會再度算出「明天同一時間」——設一次的鬧鐘天天響。
    if not week and when is None:
        when = _next_date_for(hhmm)
    item = {
        "id": _new_id("a"),
        "time": hhmm,
        "label": str(label or "").strip()[:100],
        "days": week,
        "date": when,
        "enabled": True,
    }
    with _LOCK:
        data = _load()
        data["alarms"].append(item)
        data["alarms"].sort(key=lambda a: a["time"])
        _save(data)
    return item


# ── 記帳 ─────────────────────────────────────────────────────────────────────
def add_entry(
    amount: float,
    category: str = "其他",
    note: str = "",
    ts: str | None = None,
    income: bool = False,
) -> dict[str, Any]:
    """記一筆帳。amount 一律給正數，是收入就把 income 設為 true。"""
    amt = _need_amount(amount)
    cat = str(category or "").strip() or "其他"
    if cat not in CATEGORIES:
        raise AgendaError(f"category 只能是這幾個之一：{'、'.join(CATEGORIES)}")
    item = {
        "id": _new_id("l"),
        "amount": amt,
        "category": cat,
        "note": str(note or "").strip()[:200],
        # 沒給時間就是現在——記帳絕大多數是當下記的，強迫填時間只會讓人懶得記
        "ts": _need_datetime(ts, "ts") if ts else datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "income": bool(income),
    }
    with _LOCK:
        data = _load()
        data["ledger"].append(item)
        data["ledger"].sort(key=lambda x: x["ts"], reverse=True)
        _save(data)
    return item


def summarize(month: str | None = None) -> dict[str, Any]:
    """某個月的收支結算。month 給 YYYY-MM，不給就是這個月。"""
    m = str(month or "").strip() or date.today().strftime("%Y-%m")
    if not re.match(r"^\d{4}-\d{2}$", m):
        raise AgendaError(f"month 要寫成 YYYY-MM，收到 {month!r}")
    rows = [r for r in _load()["ledger"] if str(r.get("ts", "")).startswith(m)]
    expense = sum(r["amount"] for r in rows if not r.get("income"))
    income = sum(r["amount"] for r in rows if r.get("income"))
    by_cat: dict[str, float] = {}
    for r in rows:
        if not r.get("income"):
            by_cat[r["category"]] = round(by_cat.get(r["category"], 0) + r["amount"], 2)
    return {
        "month": m,
        "expense": round(expense, 2),
        "income": round(income, 2),
        "net": round(income - expense, 2),
        "count": len(rows),
        # 由多到少排：看月結第一眼要問的是「錢花在哪」，不是「有哪些分類」
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
    }


# ── 課表 ─────────────────────────────────────────────────────────────────────
# 一堂課一筆，同一門課一週上兩次就是兩筆（同名不同 day）。
#
# 另一種寫法是一筆課帶一個時段陣列，看起來省事，實際上每個讀的人都得先展開才知道
# 「週三第五節有沒有課」，而編輯一個時段要先在陣列裡找到它。課表的每一個操作
# （這格是什麼課、把這格刪掉、這格改教室）都是針對「某天某節」，資料就照那個形狀存。

def add_course(
    name: str,
    day: Any,
    from_period: Any,
    to_period: Any = None,
    teacher: str = "",
    room: str = "",
    note: str = "",
) -> dict[str, Any]:
    """新增一堂課。to_period 不給就等於 from_period（只有一節）。"""
    if not str(name).strip():
        raise AgendaError("課程要有名稱")
    lo = _need_period(from_period, "from_period")
    hi = _need_period(to_period, "to_period") if to_period else lo
    if hi < lo:
        raise AgendaError(f"to_period（{hi}）不能比 from_period（{lo}）早")
    item = {
        "id": _new_id("c"),
        "name": str(name).strip()[:60],
        "day": _need_day(day),
        "from_period": lo,
        "to_period": hi,
        "teacher": str(teacher or "").strip()[:40],
        "room": str(room or "").strip()[:40],
        "note": str(note or "").strip()[:200],
    }
    with _LOCK:
        data = _load()
        data["courses"].append(item)
        data["courses"].sort(key=lambda c: (c["day"], c["from_period"]))
        _save(data)
        # 衝堂只回報不阻擋：重修、跨系選課本來就會撞，替使用者決定「這不合法」是越權。
        # 但也不能沉默——助理重複新增同一堂課時，這是唯一看得出來的徵兆。
        # 在鎖內用剛存下去的同一份算：出鎖後再讀一次不只多解析一遍整檔，
        # 拿到的還可能是別人已經改過的新快照，回報的衝堂跟剛存的這筆對不起來。
        hits = conflicts_for(item, data["courses"])
    return {**item, "conflicts": hits}


def conflicts_for(
    course: dict[str, Any], pool: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """跟這堂課在同一天、節次有重疊的其他課。

    `pool` 給的話就拿它比對，不再自己讀檔（用意同 [courses_on] 的 `data`）。
    """
    return [
        c for c in (pool if pool is not None else _load()["courses"])
        if c["id"] != course.get("id")
        and c["day"] == course["day"]
        # 兩個區間重疊的條件：各自的開始都不晚於對方的結束
        and c["from_period"] <= course["to_period"]
        and course["from_period"] <= c["to_period"]
    ]


def courses_on(day: Any, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """某一天的課，由早到晚。day 的編號同 [_need_day]（0=週一）。

    `data` 給的話就用它，不再自己讀檔——同一次操作要看好幾類資料時，
    每個函式各讀一遍不只多解析幾次整檔，拿到的還可能是不同時間點的快照。
    """
    want = _need_day(day)
    rows = (data if data is not None else _load())["courses"]
    return sorted((c for c in rows if c["day"] == want), key=lambda c: c["from_period"])


def get_periods(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """節次時間表，依節次排好。`data` 的用意同 [courses_on]。"""
    rows = (data if data is not None else _load())[PERIODS_KEY]
    return sorted(rows, key=lambda p: p["no"])


def set_periods(rows: Any) -> list[dict[str, Any]]:
    """整份改寫節次時間表。

    整份而不是逐節改：這是一張表，改一節的時間常常連帶要推後面幾節，
    一次一格地改會在中間留下前後矛盾的狀態（第三節結束時間晚於第四節開始）。
    """
    if not isinstance(rows, (list, tuple)) or not rows:
        raise AgendaError("periods 要給一個陣列，每個元素長這樣："
                          '{"no": 1, "start": "08:10", "end": "09:00"}')
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for r in rows:
        if not isinstance(r, dict):
            raise AgendaError(f"periods 裡有不是物件的東西：{r!r}")
        no = _need_period(r.get("no"), "no")
        if no in seen:
            raise AgendaError(f"第 {no} 節出現了兩次")
        seen.add(no)
        start = _need_time(r.get("start"), f"第 {no} 節的 start")
        end = _need_time(r.get("end"), f"第 {no} 節的 end")
        if end <= start:
            raise AgendaError(f"第 {no} 節的結束時間（{end}）要晚於開始時間（{start}）")
        out.append({"no": no, "start": start, "end": end})
    out.sort(key=lambda p: p["no"])
    # 節與節之間不可以重疊。原本只查單節自己的 end > start 與 no 沒重複，
    # 於是「第三節 10:00-12:00、第四節 11:00-12:00」是合法的——而 now_status
    # 靠 `next(p for p in periods if start <= now <= end)` 取**第一個**命中，
    # 「現在第幾節」就變成看排序運氣的任意值，跨節課程的判斷跟著一起錯。
    for prev, cur in zip(out, out[1:]):
        if cur["start"] < prev["end"]:
            raise AgendaError(
                f"第 {cur['no']} 節的開始時間（{cur['start']}）早於"
                f"第 {prev['no']} 節的結束時間（{prev['end']}），兩節重疊了")
    with _LOCK:
        data = _load()
        data[PERIODS_KEY] = out
        _save(data)
    return out


def now_status() -> dict[str, Any]:
    """現在是星期幾第幾節、正在上什麼課、下一堂是什麼。

    助理問「他現在有課嗎」用這個，不要自己拿課表跟時間去比對——
    節次對照與跨節課程那兩件事很容易算錯，而算錯的結果是在他上課時打擾他。
    """
    now = datetime.now()
    hhmm = now.strftime("%H:%M")
    day = now.weekday()          # Python 的 weekday() 也是 0=週一，剛好同一套
    # 整份只讀一次再分給兩個查詢用。原本 get_periods 與 courses_on 各讀一遍，
    # 「現在第幾節」與「今天有哪些課」因此可能是兩個不同時間點的快照——
    # 中間只要有人改過課表，就會算出「第三節，但今天的課裡沒有第三節」這種
    # 自相矛盾的結果，而這個回傳正是用來決定要不要打擾使用者的。
    data = _load()
    periods = get_periods(data)
    today = courses_on(day, data)
    # 現在落在哪一節。節與節之間的下課時間不屬於任何一節，回 None。
    period = next(
        (p["no"] for p in periods if p["start"] <= hhmm <= p["end"]), None,
    )
    span = {p["no"]: p for p in periods}

    def started(c: dict[str, Any]) -> str:
        return span.get(c["from_period"], {}).get("start", "")

    current = None
    if period is not None:
        current = next(
            (c for c in today if c["from_period"] <= period <= c["to_period"]), None,
        )
    # 下一堂：今天還沒開始的第一堂。跨天不找——「下一堂在三天後」對使用者沒意義，
    # 那種問題該看整份課表。
    nxt = next((c for c in today if started(c) > hhmm), None)
    return {
        "now": now.strftime("%Y-%m-%dT%H:%M"),
        "weekday": day,
        "period": period,
        "current": current,
        "next": nxt,
        "next_start": started(nxt) if nxt else None,
        "today": today,
    }


# ── 共用的讀取與修改 ─────────────────────────────────────────────────────────
def list_all() -> dict[str, list[dict[str, Any]]]:
    """四類加節次表全給。App 打開日常頁就是要全部，分幾次拉只是多幾趟往返。"""
    return _load()


def list_kind(kind: Kind) -> list[dict[str, Any]]:
    return _load()[kind]


def update(kind: Kind, item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """部分更新。只認得該類別本來就有的欄位，其餘忽略。

    忽略而不是報錯，是因為 App 端可能把整筆物件送回來（含 id），
    為了這個要求呼叫端先過濾欄位並不值得。
    """
    with _LOCK:
        data = _load()
        for row in data[kind]:
            if row["id"] != item_id:
                continue
            for k, v in patch.items():
                if k == "id" or k not in row:
                    continue
                row[k] = _coerce(kind, k, v)
            _check_row(kind, row)
            if kind == "events":
                data["events"].sort(key=lambda e: e["start"])
            elif kind == "alarms":
                data["alarms"].sort(key=lambda a: a["time"])
            elif kind == "courses":
                data["courses"].sort(key=lambda c: (c["day"], c["from_period"]))
            _save(data)
            return row
    raise AgendaError(f"找不到這筆：{item_id}")


def _check_row(kind: Kind, row: dict[str, Any]) -> None:
    """跨欄位驗證，必要時就地補齊。

    `_coerce` 是逐欄位的，看不到「兩個欄位之間」的關係，而新增時是有檢查的：
    `add_course` 擋 `to_period < from_period`。`update` 少了這一關就繞得過去，
    而課表一旦出現 `from=5, to=1`，`conflicts_for` 與 `now_status` 的區間判斷
    （兩邊都要求 from <= to）就永遠不成立——「他現在在上課嗎」一律答沒有，
    助理於是在上課時間打擾他。這種錯不會報錯，只會安靜地給錯答案。

    金額的正數檢查放在 `_need_amount` 裡，那是單欄位的事，這裡不重複。
    """
    if kind == "courses" and row["to_period"] < row["from_period"]:
        raise AgendaError(
            f"to_period（{row['to_period']}）不能比 from_period（{row['from_period']}）早")
    # 一次性鬧鐘一定要有明確日期，理由與 `add_alarm` 裡那段完全相同：留 null 的話
    # 手機端只能理解成「下一次到這個時間」，而它響完會重排，於是**天天響**。
    # add_alarm 有補，update 沒補——把每週重複的鬧鐘改成 `days: []` 就從缺口鑽過去了，
    # 而使用者的意思明明是「以後只響這一次」。
    if kind == "alarms" and not row.get("days") and row.get("date") is None:
        row["date"] = _next_date_for(row["time"])


def _coerce(kind: Kind, field: str, v: Any) -> Any:
    """更新單一欄位時套用跟新增時同一套驗證。"""
    if field == "start":
        return _need_datetime(v, "start")
    if field == "end":
        return _need_datetime(v, "end") if v else None
    if field == "ts":
        return _need_datetime(v, "ts")
    if field == "time":
        return _need_time(v, "time")
    if field == "days":
        return _need_days(v)
    if field == "day":
        return _need_day(v)
    if field in ("from_period", "to_period"):
        return _need_period(v, field)
    if field == "date":
        return _opt_date(v, "date")
    if field in ("enabled", "done", "income"):
        return bool(v)
    if field == "remind_min":
        return max(0, _need_int(v, "remind_min"))
    if field == "amount":
        return _need_amount(v)
    if field == "category":
        if v not in CATEGORIES:
            raise AgendaError(f"category 只能是：{'、'.join(CATEGORIES)}")
        return v
    return str(v)


def remove(kind: Kind, item_id: str) -> bool:
    with _LOCK:
        data = _load()
        before = len(data[kind])
        data[kind] = [r for r in data[kind] if r["id"] != item_id]
        if len(data[kind]) == before:
            return False
        _save(data)
        return True


def upcoming(limit: int = 20) -> list[dict[str, Any]]:
    """還沒過期也還沒完成的行程，由近到遠。助理問「我最近有什麼事」用這個。"""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    rows = [e for e in _load()["events"] if not e.get("done") and e["start"] >= now]
    return rows[:limit]
