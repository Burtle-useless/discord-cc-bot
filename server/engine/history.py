"""從 CC 的 session 逐字稿重建對話歷史。

存在的理由：事件流是「當下發生什麼」，不是「曾經發生什麼」。
App 冷啟動、重裝、切對話時沒有事件可看，畫面就是空的。
歷史的權威來源是 CC 自己寫的 session jsonl——我們不另外存一份，
避免兩份紀錄不同步（那會比沒有歷史更糟）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import config

from .fold import ask_payload, clean_reply
from .state import _load_map
from .toolinfo import tool_info

# 這些開頭代表「這一則不是人打的」。CC 的 user turn 是個大雜燴：除了真的使用者
# 訊息，還混著補跑提示、斜線指令條目、背景任務通知、讀圖工具回填的尺寸註記……
# 全部長得跟使用者自己說的話一模一樣，重建歷史時就原樣畫成一顆訊息氣泡。
# 2026-08-14 使用者回報「這些訊息不應該出現在使用者的介面」，截圖裡是整片
# <task-notification> 的英文 XML 卡在他自己的兩句話中間。
#
# 判準用開頭而不是包含：使用者本人完全可能在對話裡提到 /compact 或 task-notification。
# 實測這些注入各自獨佔一則 record，不會跟使用者的話混在同一則，所以整則丟掉是安全的。
#
# 文案改了要回來同步。history 不能反過來 import turn 取那幾個 NUDGE 常數——
# turn → client_pool → history 已經是一條依賴鏈，接上去就繞成環。
_OPS_PREFIXES: Final[tuple[str, ...]] = (
    "剛才那一步還沒收尾",              # turn.CONTINUE_NUDGE
    "剛才沒有收到",                    # turn.EMPTY_RETRY_NUDGE
    "剛才這一輪中途觸發了自動壓縮",      # turn.COMPACT_RECHECK_NUDGE
    "/compact",                       # turn.COMPACT_PROMPT
    "請把我們目前為止的對話壓縮成重點摘要",  # 改用 /compact 之前的假壓縮殘骸
    "<command-name>",                 # CLI 把斜線指令記成這種條目
    "<local-command",                 # 同上，指令的輸出與警語
    "<task-notification>",            # 背景任務結束時 SDK 塞進 user turn 的通知
    "〔背景工作回報",                  # 2026-08-25～09-02 之間 butler 自己送的續跑訊息；
                                      # 已改由 CLI 原生 wake 週期接手，留著濾舊逐字稿
    "<system-reminder>",
    "[Image: original ",              # 讀圖後回填的尺寸換算註記
    "This session is being continued from a previous conversation",
)

# 送進模型前蓋在使用者訊息前面的時間戳（見 turn._stamp）。那是給模型算時間用的，
# 不是他打的字，畫回氣泡之前要剝掉——不剝的話每一則訊息開頭都會多出一串
# 他自己沒寫過的 `[08/21 週四 11:04]`，而且只在重開 App 之後才出現。
#
# 格式對不上就整串留著：寧可漏剝一則，也不要拿一個寬鬆的正則去吃掉使用者
# 真的用中括號開頭寫的話。
# 不是助理說的話、卻以 assistant 身分躺在逐字稿裡的句子：額度用盡的英文、
# 「這輪沒回應」的預設 result。整則濾掉。
_NOISE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(You've hit your [\w\s]*limit\b.*|No response requested\.?)$",
    re.IGNORECASE | re.DOTALL,
)

# 插話訊息的前綴（`runner.STEER_PREFIX`，全形方括號包一句話再換行）。這裡用正則
# 而不 import runner：runner → client_pool → history 已經是一條依賴鏈。
_STEER_RE: Final[re.Pattern[str]] = re.compile(r"^〔插話｜[^〕]*〕\s*")

_STAMP_RE: Final[re.Pattern[str]] = re.compile(
    # 尾巴那截是來源（手機／電腦），2026-08-23 才加，舊訊息沒有，所以是選配。
    # 限定中文兩三個字而不是 `.*`，理由同上：別讓它有機會吃掉真的內容
    r"^\[\d{2}/\d{2} 週[一二三四五六日] \d{2}:\d{2}(?: [一-鿿]{2,3})?\] "
)


def _session_file(session_id: str) -> Path | None:
    for jf in config.claude_projects_dir().glob(f"*/{session_id}.jsonl"):
        return jf
    return None


def session_exists(session_id: str | None) -> bool:
    """這個 session 還接得回去嗎。

    給 client_pool 在帶 resume 前擋一下。CLI resume 一個不存在的 session 時
    會直接 exit 1，而 stderr 那句「No conversation found with session ID」
    **不會進到 SDK 的例外訊息**——Python 這側只拿得到
    「Command failed with exit code 1 / Check stderr output for details」。
    也就是說錯誤分類器看不出這是 session 的問題，於是既不丟 client 也不清
    session，下一次照樣帶著同一個壞 id 去撞：這條對話就永遠回不了話。
    （2026-08-12 以 _diag_resume.py 實測確認。）

    所以不靠事後解析錯誤字串，改成事前確認檔案還在。
    """
    return bool(session_id) and _session_file(str(session_id)) is not None


def _text_of(content: Any) -> str:
    """把 message.content 抽成純文字（忽略 thinking／tool_use 等區塊）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


# 單則歷史訊息能帶多少思考。助理平常一則幾百字，這是安全網不是常態上限——
# 歷史一次回 60 則，不設限的話一個長回合就能讓 snapshot 膨脹到幾 MB，
# 手機在外面用行動網路切個對話要等半天。
_THINK_MAX: Final[int] = 3000


def _thinking_of(content: Any) -> str:
    """抽出 assistant 訊息裡的思考區塊。

    逐字稿裡的形狀是 `{"type":"thinking","thinking":"…","signature":"…"}`
    （2026-08-17 以助理的 session 實測確認，assistant 訊息只有 text 與 thinking 兩種區塊）。
    """
    if not isinstance(content, list):
        return ""
    return "\n\n".join(
        b.get("thinking", "") for b in content
        if isinstance(b, dict) and b.get("type") == "thinking"
    ).strip()


# 歷史裡每則訊息最多帶幾次工具呼叫，每條指令原文留多長。
#
# 兩個都是體積護欄。`tool_info` 的 raw 是**刻意不截斷**的完整指令原文，那是
# 「核對它到底在跑什麼」的防線（見 toolinfo 模組說明）——但那條防線的作用在
# 指令跑之前，事後回看已經沒有攔截可言，體積卻照樣要付。歷史一次回 60 則，
# 使用者 2026-08-23 抱怨過切對話載入慢（「超級無敵慢　超級煩」），剛修好，
# 不能為了軌跡再撐回去。
_TOOLS_MAX: Final[int] = 40
_TOOL_RAW_MAX: Final[int] = 2000


def _tools_of(content: Any) -> list[dict]:
    """抽出 assistant 訊息裡的工具呼叫。

    逐字稿裡的形狀是 `{"type":"tool_use","name":"Bash","input":{…},"id":"…"}`。

    走 `tool_info` 而不是自己組欄位：畫面上重建出來的軌跡跟當下串流看到的
    是同一種東西，兩邊的圖示、摘要、危險判定就不會各自走鐘。
    """
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        info = tool_info(str(b.get("name") or ""), b.get("input") or {})
        if len(info["raw"]) > _TOOL_RAW_MAX:
            info["raw"] = info["raw"][:_TOOL_RAW_MAX] + "…"
        out.append(info)
    return out


_TN_FIELD = re.compile(r"<(status|summary|description)>\s*(.*?)\s*</\1>", re.DOTALL)


def _wake_note(xml: str) -> str:
    """把 <task-notification> 的 XML 變成一行人看的字，跟即時的 wakeNoteText 同款。"""
    fields = {k: v for k, v in _TN_FIELD.findall(xml)}
    how = {"completed": "完成", "failed": "失敗", "stopped": "中止"}.get(
        fields.get("status", ""), "有結果了")
    what = fields.get("description") or ""
    if not what:
        # CLI 的 summary 長這樣：Background command "描述" completed (exit code 0)
        m = re.search(r'"([^"]{1,80})"', fields.get("summary", ""))
        what = m.group(1) if m else ""
    return f"背景工作「{what}」{how}，助理接手" if what else f"背景工作{how}，助理接手"


def _at_ms(rec: dict) -> int | None:
    """這則記錄的時間，epoch 毫秒；解析不出來回 None。

    **一定要當成 UTC 解。** 實測 2026-08-18，逐字稿的格式是帶 Z 的 UTC
    （`2026-08-10T12:30:22.938Z`），而 outbox 的登記時間是本地時間、不帶時區。
    要把兩邊排在同一條時間軸上，漏掉時區就是整整差八小時——不會拋例外、
    不會有 log，只會把檔案卡片安靜地排到八小時外的某個位置去。
    """
    ts = rec.get("timestamp")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    # 沒帶時區的話寧可不猜。猜錯的代價跟上面那八小時是同一種。
    if dt.tzinfo is None:
        return None
    return int(dt.timestamp() * 1000)


# 從檔尾往回讀多少，湊不滿 `limit` 則訊息就換下一段。
#
# 逐字稿會長到很大——助理那條與這條各自 220MB——而畫面只要最後 60 則。先前是
# 從頭讀到尾、解析每一行、最後才切 `out[-limit:]`，於是切一次對話要 1~2.4 秒
# （量測：220MB→2.4s、8.7MB→125ms、0.6MB→14ms，跟檔案大小成正比）。
# 使用者 2026-08-23 的原話是「載入速度超級無敵慢　超級煩」。
#
# 4MB 通常就裝得下 60 則對話；裝不下的（工具軌跡與圖片佔掉大半那種）再往前
# 抓一段。最後一格是 None＝整份都讀，行為退回原本那樣，答案永遠不會比以前差。
_TAIL_STEPS: Final[tuple[int | None, ...]] = (4 << 20, 32 << 20, None)

# snapshot 與 /history 一頁的則數
HISTORY_PAGE: Final[int] = 60


def _tail_lines(jf: Path, nbytes: int | None) -> tuple[list[str], bool]:
    """取檔案最後 [nbytes] 個位元組裡的完整行。

    回 `(行, 是不是整份檔案)`。後者讓呼叫端知道「湊不滿也不用再往前找了」。

    從中間切下去的第一行幾乎一定是半截的，直接丟掉——JSON 解析本來就會擋掉它，
    但丟在這裡意圖比較清楚。多字元編碼被切開也不怕，errors="replace" 只會
    毀掉那半行，而那半行反正要丟。
    """
    size = jf.stat().st_size
    if nbytes is None or size <= nbytes:
        with jf.open(encoding="utf-8", errors="replace") as f:
            return f.read().splitlines(), True
    with jf.open("rb") as f:
        f.seek(size - nbytes)
        raw = f.read()
    return raw.decode("utf-8", errors="replace").splitlines()[1:], False


def load_history(conv_id: str, limit: int = 60, head: int = 0) -> list[dict]:
    """重建一個對話的歷史訊息，回 [{role, text, at_ms, think?}]，舊到新。

    `at_ms` 是那則的時間（epoch 毫秒）。加它是為了讓 App 能把檔案卡片依時間
    插回原位——卡片不在逐字稿裡（那是 butler 自己的東西），重建畫面時只能靠
    時間跟訊息對齊。

    `limit` 取**最後** N 則（畫面要看的是最近的），`head` 取**最前** N 則並在
    湊滿時立刻停止讀檔。取標題只需要開頭那幾則，先前是拿 `limit=10**9` 撈全部
    再切前 20——逐字稿動輒上 MB，那等於為了開頭幾行把整份解析一遍。
    兩個參數同時給的時候 `head` 先生效（它決定讀到哪裡為止）。

    思考與工具軌跡都要重建。

    這兩樣原本是一起丟掉的，理由寫成「那是過程，事後回看價值低」。實際後果是
    它們只在「當下那一次串流」看得到：服務一重啟、App 一重裝、對話一切走再切
    回來，整條對話就只剩下光禿禿的問與答。2026-08-17 使用者回報思考不見，
    當時只把思考撿回來，工具軌跡留在原地；2026-08-25 他回報了另一半。

    「回看價值低」是錯的判斷。助理做完一件事之後，人要回頭確認的正是它到底動了
    哪些檔案、跑了什麼指令——那是**唯一**的紀錄。而且它跟思考一樣在 App 上是
    摺疊的，不佔版面。

    一個回合可能有好幾則 assistant record（每次工具往返一則），思考與工具散在
    各則、而最終回覆只在最後一則。所以兩者都是**累積**到下一則有文字的訊息上，
    還原出來的形狀才跟即時串流時看到的一樣：一段回覆配一整包該回合的過程。
    """
    rec = _load_map().get(conv_id) or {}
    sid = rec.get("session_id")
    if not sid:
        return []
    jf = _session_file(sid)
    if jf is None:
        return []

    if head:
        # 取開頭那幾則（給標題用）：從檔頭串流讀，湊滿就停，
        # 後面那幾百 MB 連碰都不必碰
        try:
            with jf.open(encoding="utf-8", errors="replace") as f:
                return _drop_answered_asks(_parse_lines(f, head))
        except OSError:
            return []

    msgs, _ = _load_tail(jf, limit, None)
    return msgs


def load_history_before(conv_id: str, before_ms: int, limit: int = 60) -> tuple[list[dict], bool]:
    """歷史分頁：取 `at_ms < before_ms` 的最後 [limit] 則（舊到新），與「還有沒有更早的」。

    snapshot 只帶最後 60 則，長對話往上捲到頂就得從這裡補。判準用時間而不是
    序號，因為逐字稿裡沒有可靠的序號可用，而 at_ms 本來就是 App 排卡片用的軸。
    """
    rec = _load_map().get(conv_id) or {}
    sid = rec.get("session_id")
    if not sid:
        return [], False
    jf = _session_file(sid)
    if jf is None:
        return [], False
    return _load_tail(jf, limit, before_ms)


def _load_tail(jf: Path, limit: int, before_ms: int | None) -> tuple[list[dict], bool]:
    """從檔尾往前讀，湊滿 [limit] 則就停；[before_ms] 給值時只算早於它的那些。

    回 `(訊息, 還有沒有更早的)`。「還有更早」的判準：篩出來的比 limit 多、
    或者這次沒讀到整份檔案（那前面一定還有東西）。
    """
    out: list[dict] = []
    whole = False
    try:
        for step in _TAIL_STEPS:
            lines, whole = _tail_lines(jf, step)
            out = _parse_lines(lines, 0)
            if before_ms is not None:
                out = [m for m in out if int(m.get("at_ms") or 0) < before_ms]
            # 多湊一則才知道「還有沒有更早的」；已經讀完整份的話再翻也翻不出東西
            if whole or len(out) > limit:
                break
    except OSError:
        return [], False
    has_more = (not whole) or len(out) > limit
    return _drop_answered_asks(out[-limit:]), has_more


def _parse_lines(lines: Iterable[str], head: int) -> list[dict]:
    """把逐字稿的行解析成歷史訊息，舊到新。[head] 給值時湊滿就停。"""
    out: list[dict] = []
    pending_think: list[str] = []
    pending_tools: list[dict] = []
    # 解析不出時間的那則沿用上一則的，時間軸才不會突然掉回 0 把後面的卡片
    # 全部擠到前面去。開頭就解析不出來只能給 0，那時本來也沒有更好的猜測。
    last_ms = 0
    for line in lines:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = r.get("type")
        if role not in ("user", "assistant"):
            continue
        # 壓縮接續摘要：CLI 做完 /compact 會塞一則帶標記的長篇英文摘要
        # 當開場 user 訊息，那是給模型看的維運產物，不是對話
        if r.get("isCompactSummary"):
            continue
        # CLI 自己標的「這則不是人打的」。載入 skill 時整份 SKILL.md 會以
        # user turn 的形狀塞進來，逐字稿裡就是一大段英文 prompt——2026-08-26
        # 使用者在對話裡看到的正是這個（載入的那份 skill 全文，
        # 頂著「你」的標籤畫成他自己說的話）。
        #
        # **這條比底下的前綴清單可靠**：它是結構欄位不是字串比對。兩條 session
        # 逐字稿實測，407 則 isMeta 沒有一則帶時間戳（`_STAMP_RE`，那是 butler
        # 蓋在真人訊息上的），也就是零誤判；順帶把 32 則
        # 「Continue from where you left off.」一起擋掉了。
        #
        # 前綴清單照樣留著：`<command-name>`、`<local-command-stdout>`、
        # 續跑提示那幾種**不帶 isMeta**，只有它們攔得住。
        if role == "user":
            # 背景工作跑完時 CLI 注入的 <task-notification>（isMeta）：即時路徑畫成
            # 「背景工作『x』完成，助理接手」那行小字，重建歷史也要有它，不然重開 App
            # 之後助理憑空多講一段話、找不到頭。轉成 role=system 的項目給前端畫成同一行
            raw_head = _text_of((r.get("message") or {}).get("content")).lstrip()
            if raw_head.startswith("<task-notification>"):
                note = _wake_note(raw_head)
                if note:
                    at = _at_ms(r)
                    if at is None:
                        at = last_ms
                    else:
                        last_ms = at
                    out.append({"role": "system", "text": note, "at_ms": at})
                continue
        if r.get("isMeta") is True:
            continue
        content = (r.get("message") or {}).get("content")
        if role == "assistant":
            th = _thinking_of(content)
            if th:
                pending_think.append(th)
            # 一定要在下面那個「沒有文字就跳過」之前累積：純工具往返的那幾則
            # record 本來就沒有文字，跳過的正是工具最多的那幾則
            pending_tools.extend(_tools_of(content))
        text = _text_of(content).strip()
        if not text:
            continue
        # 額度用盡時 CLI 讓模型「回」的那句英文會寫進逐字稿的 assistant 訊息。
        # 即時路徑現在把它當錯誤（runner 認 RateLimitEvent），重建歷史也不該
        # 讓它頂著助理的頭像出現；同款的還有續跑輪的預設 result。
        if role == "assistant" and _NOISE_RE.match(text):
            continue
        if role == "user" and text.startswith(_OPS_PREFIXES):
            # 維運注入（續跑提示等）不算新回合，累積中的過程要留給真正的回覆
            continue
        if role == "user":
            # 插話的前綴（runner.STEER_PREFIX）是給模型看的，跟時間戳一樣要剝掉；
            # 先剝它，因為它包在時間戳外面
            text = _STEER_RE.sub("", text, count=1)
            text = _STAMP_RE.sub("", text, count=1)
        at = _at_ms(r)
        if at is None:
            at = last_ms
        else:
            last_ms = at
        item: dict = {"role": role, "text": clean_reply(text), "at_ms": at}
        if role == "assistant":
            # 選項按鈕從原文的 [[ASK:]] 標記重抽。clean_reply 會把標記剝掉
            # （那是給系統讀的，不該出現在畫面上），所以一定要在剝掉之前
            # 從 text 抽——順序寫反的話按鈕在重開 App 後就永遠不見了。
            ask = ask_payload(text)
            if ask is not None:
                item["ask"] = ask
        if role == "assistant" and pending_think:
            th = "\n\n".join(pending_think)
            item["think"] = th if len(th) <= _THINK_MAX else th[:_THINK_MAX] + "…"
        if role == "assistant" and pending_tools:
            # 超量時留**最前面**那幾條：一個回合的頭幾步說明了它從哪裡下手，
            # 而尾巴那幾步通常是收尾與驗證，回看時前者資訊量高得多
            item["tools"] = pending_tools[:_TOOLS_MAX]
        pending_think.clear()
        pending_tools.clear()
        out.append(item)
        if head and len(out) >= head:
            break
    return out


def _drop_answered_asks(items: list[dict]) -> list[dict]:
    """只留下「還沒被回答的」那組選項。

    對話往下走了就代表他已經回答過（不管是點按鈕還是自己打字），舊訊息底下
    再掛一排按鈕只會讓人以為還要再選一次。判準就是「這則之後還有沒有他說的話」。
    """
    last_user = max(
        (i for i, it in enumerate(items) if it.get("role") == "user"), default=-1
    )
    for i, it in enumerate(items):
        if i < last_user:
            it.pop("ask", None)
    return items
