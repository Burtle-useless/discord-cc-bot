"""給助理用的行事曆／鬧鐘／記帳／課表工具（in-process MCP）。

用 SDK 的 create_sdk_mcp_server 而不是叫助理自己去改 JSON 檔：
自己改檔的話它得先讀檔、猜格式、寫回去，每一步都可能寫壞，而且寫壞了沒人知道。
走工具就有 schema 驗證，錯了當場回一句它讀得懂的中文，它會自己改。

**schema 一律寫完整的 JSON Schema**，不用 SDK 的 {"欄位": 型別} 簡寫——
簡寫會把每個欄位都塞進 required（見 create_sdk_mcp_server 的 _build_schema），
於是「提醒我明天開會」也被逼著填 end 跟 note。
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from agenda import store

# 資料變更後要通知手機重新拉（鬧鐘得重排，不然助理設的鬧鐘不會響）。
# engine 不該認識 transport，所以這裡只留一個掛勾，由 app.py 在啟動時注入。
_on_change: Callable[[str], Awaitable[None]] | None = None


def set_change_hook(fn: Callable[[str], Awaitable[None]]) -> None:
    global _on_change
    _on_change = fn


async def _changed(what: str) -> None:
    if _on_change is not None:
        await _on_change(what)


def _ok(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False)}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _schema(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


_NOW_HINT = "格式 YYYY-MM-DDTHH:MM，用本機時間，不要帶時區"

# 一次工具回傳最多幾筆。工具的輸出會原封不動進 context，沒有上限的清單
# 累積個一兩年就足以把整個 session 撐爆（見 calendar_list 的說明）。
_LIST_MAX = 200


# ── 行事曆 ───────────────────────────────────────────────────────────────────
@tool(
    "calendar_add",
    "在使用者的行事曆上新增一件行程。他說「幫我記一下明天三點要看牙醫」這種就用這個。",
    _schema({
        "title": {"type": "string", "description": "行程名稱，簡短"},
        "start": {"type": "string", "description": f"開始時間，{_NOW_HINT}"},
        "end": {"type": "string", "description": f"結束時間，可省略。{_NOW_HINT}"},
        "note": {"type": "string", "description": "備註，可省略"},
        "remind_min": {"type": "integer",
                       "description": "提前幾分鐘提醒，預設 10，填 0 代表不提醒"},
    }, ["title", "start"]),
)
async def calendar_add(args: dict[str, Any]) -> dict[str, Any]:
    try:
        item = store.add_event(
            title=args["title"], start=args["start"],
            end=args.get("end"), note=args.get("note", ""),
            # 不在這裡 int()：助理傳「稍後」這種東西進來時，ValueError 會穿過
            # 下面只接 AgendaError 的 except 變成 SDK 例外，它就看不到能自我修正的訊息
            remind_min=args.get("remind_min", 10),
        )
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("events")
    return _ok(item)


@tool(
    "calendar_list",
    "看使用者的行事曆。預設只給還沒發生的行程；要查過去的把 all 設成 true。",
    _schema({
        "all": {"type": "boolean", "description": "true 代表連過去與已完成的都給"},
    }, []),
)
async def calendar_list(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"now": datetime.now().strftime("%Y-%m-%dT%H:%M")}
    if not args.get("all"):
        out["events"] = store.upcoming()
        return _ok(out)

    # all=true 原本沒有任何上限，那是 context 被撐爆的來源：一天三筆、累積兩年
    # 就是兩千筆，一次工具回傳可以到數十萬 token，直接把 session 打成
    # CONTEXT_FULL 而重置。`upcoming()` 早就有 limit 20、`ledger_list` 也做了
    # min(limit, 200)，只有這條漏掉。
    # 截尾端而不是開頭：events 依 start 升冪排，問「以前的行程」幾乎都是在問
    # 最近發生過什麼，最舊的那幾百筆反而是最不需要的。
    rows = store.list_kind("events")
    total = len(rows)
    if total > _LIST_MAX:
        rows = rows[-_LIST_MAX:]
        # 明講被截掉了，否則助理會把「查到的」當成「全部」，
        # 回答「你今年沒有其他行程」這種完全錯誤的話。
        out["truncated"] = f"共 {total} 筆，只給最近的 {_LIST_MAX} 筆"
    out["events"] = rows
    return _ok(out)


@tool(
    "calendar_update",
    "改一件既有行程，或把它標成已完成。只要傳你想改的欄位。",
    _schema({
        "id": {"type": "string", "description": "行程 id，先用 calendar_list 查"},
        "title": {"type": "string"},
        "start": {"type": "string", "description": _NOW_HINT},
        "end": {"type": "string", "description": _NOW_HINT},
        "note": {"type": "string"},
        "remind_min": {"type": "integer"},
        "done": {"type": "boolean", "description": "標成已完成"},
    }, ["id"]),
)
async def calendar_update(args: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in args.items() if k != "id"}
    try:
        row = store.update("events", args["id"], patch)
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("events")
    return _ok(row)


@tool(
    "calendar_remove",
    "刪掉一件行程。",
    _schema({"id": {"type": "string"}}, ["id"]),
)
async def calendar_remove(args: dict[str, Any]) -> dict[str, Any]:
    gone = store.remove("events", args["id"])
    if not gone:
        return _err(f"找不到這件行程：{args['id']}")
    await _changed("events")
    return _ok({"removed": args["id"]})


# ── 鬧鐘 ─────────────────────────────────────────────────────────────────────
@tool(
    "alarm_add",
    "設一個鬧鐘。鬧鐘會在他手機上真的響（不是通知而已），所以只在他要求叫他起床或"
    "提醒他離開時用；單純記事情請改用 calendar_add。",
    _schema({
        "time": {"type": "string", "description": "24 小時制 HH:MM"},
        "label": {"type": "string", "description": "響的時候顯示什麼，可省略"},
        "days": {
            "type": "array", "items": {"type": "integer"},
            "description": "每週重複哪幾天，0=週一 6=週日。例如平日是 [0,1,2,3,4]。"
                           "不給就是只響一次",
        },
        "on_date": {"type": "string",
                    "description": "只響一次時指定哪一天，YYYY-MM-DD。不給就是下一次到那個時間"},
    }, ["time"]),
)
async def alarm_add(args: dict[str, Any]) -> dict[str, Any]:
    try:
        item = store.add_alarm(
            time=args["time"], label=args.get("label", ""),
            days=args.get("days"), on_date=args.get("on_date"),
        )
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("alarms")
    return _ok(item)


@tool("alarm_list", "看目前設了哪些鬧鐘。", _schema({}, []))
async def alarm_list(_: dict[str, Any]) -> dict[str, Any]:
    return _ok({"alarms": store.list_kind("alarms")})


@tool(
    "alarm_update",
    "改鬧鐘，或用 enabled 把它關掉／打開（關掉比刪掉好，他明天可能還要用）。",
    _schema({
        "id": {"type": "string"},
        "time": {"type": "string", "description": "HH:MM"},
        "label": {"type": "string"},
        "days": {"type": "array", "items": {"type": "integer"}},
        "enabled": {"type": "boolean"},
    }, ["id"]),
)
async def alarm_update(args: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in args.items() if k != "id"}
    try:
        row = store.update("alarms", args["id"], patch)
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("alarms")
    return _ok(row)


@tool("alarm_remove", "刪掉一個鬧鐘。", _schema({"id": {"type": "string"}}, ["id"]))
async def alarm_remove(args: dict[str, Any]) -> dict[str, Any]:
    if not store.remove("alarms", args["id"]):
        return _err(f"找不到這個鬧鐘：{args['id']}")
    await _changed("alarms")
    return _ok({"removed": args["id"]})


# ── 記帳 ─────────────────────────────────────────────────────────────────────
@tool(
    "ledger_add",
    "記一筆帳。他隨口說「剛剛午餐花了 120」就直接記進去，不用回頭問他分類，"
    "自己判斷最接近的那個。",
    _schema({
        "amount": {"type": "number", "description": "金額，一律填正數"},
        "category": {
            "type": "string", "enum": list(store.CATEGORIES),
            "description": "分類，只能用這幾個",
        },
        "note": {"type": "string", "description": "買了什麼，可省略"},
        "income": {"type": "boolean", "description": "這筆是收入的話設 true，預設是支出"},
        "ts": {"type": "string", "description": f"發生時間，不給就是現在。{_NOW_HINT}"},
    }, ["amount"]),
)
async def ledger_add(args: dict[str, Any]) -> dict[str, Any]:
    try:
        item = store.add_entry(
            amount=args["amount"], category=args.get("category", "其他"),
            note=args.get("note", ""), ts=args.get("ts"),
            income=bool(args.get("income", False)),
        )
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("ledger")
    return _ok(item)


@tool(
    "ledger_summary",
    "某個月花了多少、花在哪。他問「這個月花多少」就用這個，不要自己去加總明細。",
    _schema({
        "month": {"type": "string", "description": "YYYY-MM，不給就是這個月"},
    }, []),
)
async def ledger_summary(args: dict[str, Any]) -> dict[str, Any]:
    try:
        return _ok(store.summarize(args.get("month")))
    except store.AgendaError as e:
        return _err(str(e))


@tool(
    "ledger_list",
    "看最近的帳目明細（由新到舊）。要看總額請改用 ledger_summary。",
    _schema({
        "limit": {"type": "integer", "description": "最多幾筆，預設 30"},
    }, []),
)
async def ledger_list(args: dict[str, Any]) -> dict[str, Any]:
    try:
        limit = max(1, min(int(args.get("limit", 30)), 200))
    except (TypeError, ValueError):
        return _err("limit 要是數字，例如 30")
    return _ok({"entries": store.list_kind("ledger")[:limit]})


@tool(
    "ledger_remove", "刪掉一筆記錯的帳。",
    _schema({"id": {"type": "string"}}, ["id"]),
)
async def ledger_remove(args: dict[str, Any]) -> dict[str, Any]:
    if not store.remove("ledger", args["id"]):
        return _err(f"找不到這筆：{args['id']}")
    await _changed("ledger")
    return _ok({"removed": args["id"]})


# ── 課表 ─────────────────────────────────────────────────────────────────────
_DAY_HINT = "星期，0=週一 1=週二 … 6=週日"


@tool(
    "course_add",
    "在他的課表上加一堂課。他說「我週三三四節有電子學」這種就用這個。"
    "一門課一週上兩次就加兩筆（同名不同 day），不要試著塞成一筆。",
    _schema({
        "name": {"type": "string", "description": "課程名稱"},
        "day": {"type": "integer", "description": _DAY_HINT},
        "from_period": {"type": "integer", "description": "從第幾節開始"},
        "to_period": {"type": "integer",
                      "description": "到第幾節結束，只有一節就省略"},
        "teacher": {"type": "string", "description": "老師名字，可省略"},
        "room": {"type": "string", "description": "教室，可省略"},
        "note": {"type": "string", "description": "備註，可省略"},
    }, ["name", "day", "from_period"]),
)
async def course_add(args: dict[str, Any]) -> dict[str, Any]:
    try:
        item = store.add_course(
            name=args["name"], day=args["day"],
            from_period=args["from_period"], to_period=args.get("to_period"),
            teacher=args.get("teacher", ""), room=args.get("room", ""),
            note=args.get("note", ""),
        )
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("courses")
    # conflicts 有東西代表這格本來就有課。不是錯誤（重修、跨系選課都會撞），
    # 但要講給他聽——多半是同一堂課被加了兩次。
    return _ok(item)


@tool(
    "course_list",
    "看他的課表。回傳每一堂課與節次時間表（第幾節是幾點到幾點）。"
    "問「我明天有什麼課」也用這個，自己挑出那天的。",
    _schema({
        "day": {"type": "integer", "description": f"只看某一天，{_DAY_HINT}。不給就是整週"},
    }, []),
)
async def course_list(args: dict[str, Any]) -> dict[str, Any]:
    try:
        rows = (store.courses_on(args["day"]) if args.get("day") is not None
                else store.list_kind("courses"))
    except store.AgendaError as e:
        return _err(str(e))
    return _ok({
        "courses": rows,
        "periods": store.get_periods(),
        "today": datetime.now().weekday(),
        "day_note": "0=週一 6=週日",
    })


@tool(
    "course_now",
    "他現在有沒有在上課、下一堂是什麼。要判斷「現在方便打擾他嗎」就用這個，"
    "不要自己拿課表跟時間去比對。",
    _schema({}, []),
)
async def course_now(_: dict[str, Any]) -> dict[str, Any]:
    return _ok(store.now_status())


@tool(
    "course_update",
    "改一堂課的內容（換教室、改老師、調節次）。只要傳你想改的欄位。",
    _schema({
        "id": {"type": "string", "description": "課程 id，先用 course_list 查"},
        "name": {"type": "string"},
        "day": {"type": "integer", "description": _DAY_HINT},
        "from_period": {"type": "integer"},
        "to_period": {"type": "integer"},
        "teacher": {"type": "string"},
        "room": {"type": "string"},
        "note": {"type": "string"},
    }, ["id"]),
)
async def course_update(args: dict[str, Any]) -> dict[str, Any]:
    patch = {k: v for k, v in args.items() if k != "id"}
    try:
        row = store.update("courses", args["id"], patch)
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("courses")
    return _ok(row)


@tool(
    "course_remove", "從課表刪掉一堂課（停修、換課）。",
    _schema({"id": {"type": "string"}}, ["id"]),
)
async def course_remove(args: dict[str, Any]) -> dict[str, Any]:
    if not store.remove("courses", args["id"]):
        return _err(f"找不到這堂課：{args['id']}")
    await _changed("courses")
    return _ok({"removed": args["id"]})


@tool(
    "period_set",
    "設定節次時間表（第幾節是幾點到幾點）。每個學校不一樣，他說「我們第一節是八點」"
    "這種就用這個。**要給完整的一整份**，沒列到的節次會消失，所以先用 course_list "
    "把現有的讀出來，改完再整份送回。",
    _schema({
        "periods": {
            "type": "array",
            "description": "整份節次表，由第一節排到最後一節",
            "items": {
                "type": "object",
                "properties": {
                    "no": {"type": "integer", "description": "第幾節"},
                    "start": {"type": "string", "description": "開始時間 HH:MM"},
                    "end": {"type": "string", "description": "結束時間 HH:MM"},
                },
                "required": ["no", "start", "end"],
            },
        },
    }, ["periods"]),
)
async def period_set(args: dict[str, Any]) -> dict[str, Any]:
    try:
        rows = store.set_periods(args.get("periods"))
    except store.AgendaError as e:
        return _err(str(e))
    await _changed("periods")
    return _ok({"periods": rows})


ALL_TOOLS = [
    calendar_add, calendar_list, calendar_update, calendar_remove,
    alarm_add, alarm_list, alarm_update, alarm_remove,
    ledger_add, ledger_summary, ledger_list, ledger_remove,
    course_add, course_list, course_now, course_update, course_remove, period_set,
]

SERVER_NAME = "agenda"

# 建一次就好——server 物件無狀態（狀態都在 store 的檔案裡），
# 每次建 client 都重建只是白費工。
SERVER = create_sdk_mcp_server(
    name=SERVER_NAME, version="1.0.0", tools=ALL_TOOLS,
)
