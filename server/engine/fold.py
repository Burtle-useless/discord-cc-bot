"""訊息摺疊與文字清理。全部是純函式，可直接單元測試。"""
from __future__ import annotations

import re
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage

# CC 用來標示進度／狀態的內嵌標記。送給使用者前一律清掉，只留給控制流判定用。
#
# 中括號層數刻意寫成可有可無（`\[\[?` / `\]?\]`）：prompt 要求兩層，
# 但模型實測偶爾只給一層 [DONE]，而漏認的代價很高——標記直接洩漏到畫面上，
# 續跑判定也跟著失效、每回合都多補跑一輪。誤傷正常文字的機率遠低於這個。
MILESTONE_RE = re.compile(r"\[\[MILESTONE:\s*(.+?)\]\]")
# 「整個任務已完成」——自動續跑靠它判定不要再補刀
DONE_RE = re.compile(r"\[\[?\s*DONE\s*\]?\]", re.IGNORECASE)
# 「我問了問題、正在等使用者回答」——少了它會把「等回答」誤判成早停而逼 CC 自問自答
WAIT_RE = re.compile(r"\[\[?\s*WAIT\s*\]?\]", re.IGNORECASE)
# 「我要問他一件事，請把這些選項變成按鈕」。格式：[[ASK:問題|選項一|選項二]]
#
# 為什麼用文字標記而不是 CC 內建的 AskUserQuestion 工具：**那個工具在 SDK session
# 裡根本不存在**。實測（_diag_tools.py，2026-08-11）四種 permission_mode 的工具清單
# 都是 36 個、全都沒有它，連 CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL=1 也叫不出來。
# 模型看不到的工具不可能被呼叫，所以先前那條 ToolUseBlock 攔截路徑從頭到尾是死碼。
# 文字標記走的是 [[DONE]]／[[WAIT]] 同一套機制——那套已經證實模型會照打、正則抓得到。
ASK_RE = re.compile(r"\[\[?\s*ASK\s*:\s*(.+?)\s*\]?\]", re.IGNORECASE | re.DOTALL)

NO_RESPONSE = "__NO_RESPONSE__"   # run_claude 無輸出時的哨兵值

# CLI 對「這一輪模型什麼都沒說」的預設 result 文字（續跑提示打過去、模型決定不回
# 就會收到這句）。它不是助理說的話，卻曾經頂著助理的頭像出現在畫面上（2026-09-02
# 模擬器實見）。整句吃掉，讓它走 NO_RESPONSE 那條路。
NO_RESPONSE_RE = re.compile(r"^\s*No response requested\.?\s*$", re.IGNORECASE | re.MULTILINE)


def fold_messages(messages: list[Any]) -> tuple[str, str | None, int]:
    """把一回合收到的 SDK 訊息摺疊成 (回覆文字, session_id, ctx_tokens)。

    關鍵行為：result 優先；result 為空時退回**最後一則**有文字的 assistant 訊息。
    AskUserQuestion 收尾（result 空）要保留「問題前的說明文字」；上游 #50597 把回合
    末則 text 掉成空 thinking 時也要退回前一則實質文字——兩者都不能讓回覆變空。

    取「最後一則」而非第一則：多訊息回合（開場白→工具→最終回應）第一則是開場白、
    最後一則才是結論；只抓第一則會讓使用者只看到開場白那句（cc-bot 的歷史真 bug）。

    多個 ResultMessage（插話撲空時觀察窗回收的孤兒週期，見 runner 的插話段落）
    各自的 result 依序串接——後到的覆蓋先到的話，原回合的回覆會整段遺失。
    正常回合只有一個 Result，行為不變。
    """
    content, new_sid, ctx = "", None, 0
    last_text = ""
    parts: list[str] = []
    for m in messages:
        if isinstance(m, ResultMessage):
            if m.result:
                parts.append(m.result)
            new_sid = m.session_id or new_sid
            usage = getattr(m, "usage", None) or {}
            ctx = (usage.get("input_tokens", 0)
                   + usage.get("cache_read_input_tokens", 0)
                   + usage.get("cache_creation_input_tokens", 0)) or ctx
        elif isinstance(m, AssistantMessage):
            txt = "".join(b.text for b in m.content if hasattr(b, "text"))
            if txt.strip():
                last_text = txt
    content = "\n\n".join(parts)
    return (content or last_text), new_sid, ctx


def clean_reply(content: str) -> str:
    """清除回覆中的控制標記與 ThinkingBlock 殘留。"""
    content = MILESTONE_RE.sub("", content)
    content = DONE_RE.sub("", content)
    content = WAIT_RE.sub("", content)
    content = NO_RESPONSE_RE.sub("", content)
    # 問題本身模型會另外用白話寫在回覆裡，標記只是給系統讀的，留著就是洩漏
    content = ASK_RE.sub("", content)
    return re.sub(r"\[ThinkingBlock\(thinking=.*?\)\]", "", content, flags=re.DOTALL).strip()


def think_digest(s: str, limit: int = 220) -> str:
    """把思考壓成一行定稿摘要：壓掉換行與多餘空白、去反引號、超長截尾。

    串流中的思考是「流動」的（下一步一到就被蓋掉、看不回來），這是定稿版本，
    讓使用者事後仍捲得回去看模型每一步在想什麼。
    反引號要換掉：思考常含程式碼片段，截尾容易留下未閉合的反引號吃掉整段格式。
    """
    x = " ".join(s.split()).replace("`", "'")
    return x if len(x) <= limit else x[:limit] + "…"


def has_done(text: str) -> bool:
    return bool(DONE_RE.search(text or ""))


def has_wait(text: str) -> bool:
    return bool(WAIT_RE.search(text or ""))


def parse_ask_marker(text: str) -> dict | None:
    """從回覆原文抽出 [[ASK:問題|選項一|選項二]]，轉成 AskUserQuestion 的輸入格式。

    刻意輸出成跟那個內建工具一模一樣的結構（questions/options/label），
    讓下游的 parse_ask 與整條 ask 通道一個字都不用改——工具哪天真的出現在
    清單裡，兩條路也能並存。

    少於兩段就當沒看到：只有問題、一個選項都沒有的話，跳出去的按鈕面板是空的，
    使用者會卡在一個點不下去的對話框裡，那比不跳還糟。
    """
    m = ASK_RE.search(text or "")
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
    if len(parts) < 2:
        return None
    return {"questions": [{
        "question": parts[0],
        "options": [{"label": p, "description": ""} for p in parts[1:]],
    }]}


def ask_payload(text: str) -> dict | None:
    """從回覆原文抽出 [[ASK:]]，轉成 `reply.final` 的 `ask` 欄位格式。

    重建歷史時走這條——選項不是 CC 逐字稿裡的東西，是我們從原文的標記重新抽的，
    所以重開 App、被系統回收、換手機，同一則訊息底下的按鈕都還在。

    刻意疊在 `parse_ask_marker` 上而不是自己再 search 一次正則：兩份解析遲早
    會在某個邊界（空白、少於兩段）走鐘，而走鐘的症狀是按鈕忽有忽無，很難查。
    輸出格式與 `turn.ask_dict` 必須一致，tests/test_ask_inline.py 釘住。
    """
    parsed = parse_ask_marker(text)
    if parsed is None:
        return None
    q = parsed["questions"][0]
    return {
        "title": q["question"],
        "choices": [{"id": o["label"], "label": o["label"],
                     "detail": o.get("description", "")}
                    for o in q["options"]],
    }
