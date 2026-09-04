"""純函式與純狀態：引擎事件序列 → 畫布文字。

**不 import discord、不 import engine。** 這裡只認 `(事件型別, data dict)`，所以
`tests/test_render.py` 餵一串 dict 就能驗整個畫法，不必連 Discord、不必起引擎。

畫布長這樣（一則會被反覆編輯的 Discord 訊息）：

    -# 📥 原文前 80 字                         ← 頂部一行（turn.start）
    -# 💭 思考摘要…                             ← 每步的思考定稿（step.commit）
    這一步要做什麼的說明                          ← 動手前的說明（step.commit 的 text）
    -# 讀 3 個檔案・執行 2 個指令　**+12 −3**     ← 一段的工具統計（沿用舊 _fmt_seg_summary）
    -# ||原文||                                 ← 該段指令原文，短的直接遮罩
    ⚠️ ⚙️ **Bash**                              ← 破壞性指令單獨完整列
    ```
    rm -rf x
    ```
                                               ← 空一行
    🌓 **思考中** `12s`　🧠 `opus·high`　📈 `23%`  ← 狀態列（status）
    ✍️ 生成中 `123 字`　> 尾段…                  ← 生成中的文字尾段

分段的規則跟舊 cc-bot 一樣：一段＝CC 的一句說明＋其下所有工具呼叫；工具不逐行列，
收成一行統計。破壞性指令是唯一例外，完整單獨列行——摺疊會把指令藏起來，
「核對它到底在跑什麼」的防線就沒了。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .i18n import t

# Discord 單則 2000 字，留餘裕
MAX_MSG = 1900
# 一段的指令原文合計超過這個長度就不直接遮罩，改掛「詳細」按鈕
SPOILER_MAX = 300
# 頂部原文顯示長度
HEADER_MAX = 80
# 思考定稿一行的上限。engine 給到 2000 字，Discord 畫布放不下那麼多
THINK_DIGEST_MAX = 300
# 狀態列裡生成中文字／思考的尾段長度
LIVE_TAIL = 180
THINK_TAIL = 120
# 狀態列的秒數顯示以這個粒度取整：每則 status 都帶新的 elapsed，照原值畫就是
# 每兩秒改一次內容、每兩秒 edit 一次。取整到 5 秒，閒置時 edit 頻率跟著降到五秒一次
ELAPSED_STEP = 5

# 時間戳前綴（engine.turn.stamp 蓋的）：`[09/03 週四 20:07 Discord] `。來源可能是
# 中文（手機／電腦）或 ASCII（Discord），限制字元集免得吃到真的內容
_STAMP_RE = re.compile(r"^\[\d{2}/\d{2} 週[一二三四五六日] \d{2}:\d{2}(?: [A-Za-z一-鿿]{2,12})?\] ")

# 回覆裡的控制標記，畫半截（STOPPED）與尾段預覽前要清掉（跟 engine.fold 同一組正則）
_MILESTONE_RE = re.compile(r"\[\[MILESTONE:\s*(.+?)\]\]")
_DONE_RE = re.compile(r"\[\[?\s*DONE\s*\]?\]", re.IGNORECASE)
_WAIT_RE = re.compile(r"\[\[?\s*WAIT\s*\]?\]", re.IGNORECASE)
_ASK_RE = re.compile(r"\[\[?\s*ASK\s*:\s*(.+?)\s*\]?\]", re.IGNORECASE | re.DOTALL)

_SPINNER = ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘")


# ── 小工具 ───────────────────────────────────────────────────────────────────
def strip_stamp(text: str) -> str:
    """去掉 engine 蓋的時間戳前綴；`[名字]: ` 留著，多人頻道要看得出誰說的。"""
    return _STAMP_RE.sub("", text or "", count=1)


def one_line(s: str) -> str:
    """壓成一行：換行與連續空白收成一個空格。"""
    return " ".join((s or "").split())


def no_ticks(s: str) -> str:
    """反引號換成單引號：思考與指令常含程式碼片段，截尾容易留下未閉合的反引號吃掉整則格式。"""
    return (s or "").replace("`", "'")


def think_digest(s: str, limit: int = THINK_DIGEST_MAX) -> str:
    x = no_ticks(one_line(s))
    return x if len(x) <= limit else x[:limit] + "…"


def clean_reply(content: str) -> str:
    """清掉控制標記（跟 engine.fold.clean_reply 同一組規則，這裡不 import engine）。"""
    content = _MILESTONE_RE.sub("", content or "")
    content = _DONE_RE.sub("", content)
    content = _WAIT_RE.sub("", content)
    content = _ASK_RE.sub("", content)
    return content.strip()


def header_line(prompt: str, origin: str, wake: dict | None) -> str:
    """畫布頂部那一行。origin=wake 時是「背景工作完成，陸接手」，不是使用者原文。"""
    if origin == "wake":
        desc = one_line(str((wake or {}).get("desc") or ""))
        return t("canvas_wake", desc=no_ticks(desc)[:60]) if desc else t("canvas_wake_plain")
    text = no_ticks(one_line(strip_stamp(prompt)))
    if len(text) > HEADER_MAX:
        text = text[:HEADER_MAX] + "…"
    return t("canvas_header", text=text)


def fence(raw: str) -> str:
    """把原文包進程式碼區塊；原文裡的三反引號會提早關閉區塊，換成三個單引號。"""
    body = (raw or "").replace("```", "'''")
    return f"```\n{body}\n```"


# ── 段落統計（沿用舊 cc-bot 的 _new_seg／_seg_add／_fmt_seg_summary）──────────
@dataclass(slots=True)
class Seg:
    """一段的累積器：分類計數、動過的檔名、增刪行數、指令原文。"""

    reads: int = 0
    cmds: int = 0
    searches: int = 0
    webs: int = 0
    others: int = 0
    created: list[str] = field(default_factory=list)
    edited: list[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    raws: list[str] = field(default_factory=list)      # Bash／PowerShell 的完整指令

    def empty(self) -> bool:
        return not (self.reads or self.cmds or self.searches or self.webs or self.others
                    or self.created or self.edited)

    def add(self, d: dict) -> None:
        """把一個 tool.call 事件計進來。d 是 engine.toolinfo.tool_info 的形狀。"""
        tool = str(d.get("tool") or "")
        kind = str(d.get("kind") or "other")
        if kind == "read":
            self.reads += 1
        elif tool == "Write":
            name = str(d.get("file") or "")
            if name:
                if name in self.edited:
                    self.edited.remove(name)   # 改壞重寫：最終是整檔重寫，只列「新增」
                if name not in self.created:
                    self.created.append(name)
            self.added += int(d.get("added") or 0)
        elif kind == "edit":
            name = Path(str(d.get("summary") or "")).name
            if name and name not in self.created and name not in self.edited:
                self.edited.append(name)
            self.added += int(d.get("added") or 0)
            self.removed += int(d.get("removed") or 0)
        elif kind == "cmd":
            self.cmds += 1
            raw = str(d.get("raw") or "").strip()
            if raw:
                self.raws.append(raw)
        elif kind == "search":
            self.searches += 1
        elif kind == "web":
            self.webs += 1
        else:
            self.others += 1


def seg_names(names: list[str]) -> str:
    """檔名清單：最多列兩個，其餘收成 +N。檔名包進 code span，底線才不會被當標記吃掉。"""
    disp = ", ".join("`" + n.replace("`", "'") + "`" for n in names[:2])
    return disp + (f" +{len(names) - 2}" if len(names) > 2 else "")


def fmt_seg_summary(seg: Seg) -> str:
    """段落統計 → 一行小字（-# 子文字）。空段回空字串。"""
    parts: list[str] = []
    if seg.reads:
        parts.append(t("seg_read", n=seg.reads))
    if seg.searches:
        parts.append(t("seg_search", n=seg.searches))
    if seg.cmds:
        parts.append(t("seg_cmds", n=seg.cmds))
    if seg.created:
        parts.append(t("seg_created", names=seg_names(seg.created)))
    if seg.edited:
        parts.append(t("seg_edited", names=seg_names(seg.edited)))
    if seg.webs:
        parts.append(t("seg_web", n=seg.webs))
    if seg.others:
        parts.append(t("seg_other", n=seg.others))
    if not parts:
        return ""
    line = "・".join(parts)
    if seg.added or seg.removed:
        line += f"　**+{seg.added} −{seg.removed}**"
    return f"-# {line}"


@dataclass(frozen=True, slots=True)
class RawOut:
    """一段的指令原文該怎麼呈現：短的直接給遮罩行，長的交出全文讓呼叫端存倉庫掛按鈕。"""

    line: str | None      # 直接放進軌跡的行（`-# ||…||`）
    long: str | None      # 超過 SPOILER_MAX 的全文；None＝沒有


def spoiler_for(raws: list[str]) -> RawOut | None:
    """把一段的指令原文收成遮罩。多條指令用 ⏎ 接在同一行——多行遮罩在手機上會散開。"""
    if not raws:
        return None
    joined = " ⏎ ".join(one_line(r) for r in raws)
    # 遮罩用 || 當定界，原文裡的 || 會提早結束遮罩
    joined = joined.replace("||", "| |")
    if len(joined) <= SPOILER_MAX:
        return RawOut(line=f"-# ||{joined}||", long=None)
    return RawOut(line=None, long="\n\n".join(raws))


def danger_block(d: dict) -> str:
    """破壞性指令：一行標題＋完整原文區塊，不摘要不截尾。"""
    head = t("trace_danger", icon=str(d.get("icon") or "⚙️"), tool=str(d.get("tool") or ""))
    raw = str(d.get("raw") or "") or str(d.get("summary") or "")
    return f"{head}\n{fence(raw)}" if raw else head


# ── Markdown 表格 → 等寬區塊 ─────────────────────────────────────────────────
_SEP_ROW = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _width(s: str) -> int:
    """顯示寬度：CJK 全形算 2，等寬對齊才不會歪。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def _table_to_block(rows: list[str]) -> str:
    grid = [_cells(r) for r in rows if not _SEP_ROW.match(r)]
    ncol = max((len(r) for r in grid), default=0)
    grid = [r + [""] * (ncol - len(r)) for r in grid]
    widths = [max((_width(r[i]) for r in grid), default=0) for i in range(ncol)]
    out: list[str] = []
    for i, r in enumerate(grid):
        out.append("  ".join(_pad(c, widths[j]) for j, c in enumerate(r)).rstrip())
        if i == 0 and len(grid) > 1:
            out.append("  ".join("-" * w for w in widths))
    return "```\n" + "\n".join(out) + "\n```"


def pipe_tables_to_code(md: str) -> str:
    """Discord 不畫 Markdown 表格：把連續的 `| a | b |` 行（含分隔列）轉成等寬區塊。

    只認「至少兩行、其中有一行是 |---| 分隔列」的區塊，單獨一行含直線符號的文字不動。
    程式碼區塊裡的內容原樣保留。
    """
    lines = (md or "").split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            i += 1
            continue
        if not in_code and line.strip().startswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            block = lines[i:j]
            if len(block) >= 2 and any(_SEP_ROW.match(r) for r in block):
                out.append(_table_to_block(block))
                i = j
                continue
        out.append(line)
        i += 1
    return "\n".join(out)


# ── 切段（不截尾）────────────────────────────────────────────────────────────
def split_message(text: str, limit: int = MAX_MSG) -> list[str]:
    """把長文切成每則不超過 [limit] 的幾段，一個字都不丟。

    優先在換行切、其次空白、最後硬切。跨段的程式碼區塊會在段尾補關、下一段補開，
    不然後半段整則會變成等寬字。
    """
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    rest = text
    carry_fence = False
    while rest:
        budget = limit - (4 if carry_fence else 0) - 4   # 留給補開／補關的 ```
        if len(rest) <= budget:
            piece, rest = rest, ""
        else:
            cut = rest.rfind("\n", 0, budget)
            if cut < budget // 2:
                cut = rest.rfind(" ", 0, budget)
            if cut < budget // 2:
                cut = budget
            piece, rest = rest[:cut], rest[cut:].lstrip("\n")
        if carry_fence:
            piece = "```\n" + piece
        open_fence = piece.count("```") % 2 == 1
        if open_fence and rest:
            piece = piece + "\n```"
        carry_fence = open_fence and bool(rest)
        chunks.append(piece)
    return chunks


# ── 畫布狀態 ─────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class CanvasState:
    """一則使用者訊息從頭到尾的畫布內容。餵事件進來，隨時能 render 成文字。

    `ctx_limit` 是回 context 上限的函式（engine 的 turn.ctx_limit 要拿 ConvState，
    這裡不認識它，由 canvas 注入）；回 0 就不畫百分比。
    """

    ctx_limit: Callable[[], int] = lambda: 0
    header: str = ""
    lines: list[str] = field(default_factory=list)     # 已定稿的軌跡
    seg: Seg = field(default_factory=Seg)
    pending_step: str = ""      # 最近一步的文字，等下一個事件決定它是說明（進軌跡）還是回覆（丟）
    long_raws: list[str] = field(default_factory=list)  # 等呼叫端存倉庫掛按鈕的原文
    live_text: str = ""
    live_think: str = ""
    elapsed: float = 0.0
    model: str = ""
    effort: str | None = None
    ctx_tokens: int = 0
    tools: int = 0
    phase: str = ""             # "" / waiting / compacting
    note: str = ""
    resets_at: float | None = None
    bg: list[dict] = field(default_factory=list)
    used_tool: bool = False
    stopped: bool = False
    status_cleared: bool = False
    error_text: str = ""
    turns: int = 0              # 收到幾個 turn.start（續跑輪次）

    # ── 餵事件 ──
    def feed(self, type_: str, d: dict) -> None:
        if type_ == "turn.start":
            self.turns += 1
            self.status_cleared = False
            self.error_text = ""
            self.live_text = ""
            self.live_think = ""
            self.pending_step = ""
            if not self.header:
                self.header = header_line(
                    str(d.get("prompt") or ""), str(d.get("origin") or "user"), d.get("wake"),
                )
        elif type_ == "thinking.delta":
            self.live_think = (self.live_think + str(d.get("d") or ""))[-4000:]
        elif type_ == "text.delta":
            self.live_text += str(d.get("d") or "")
        elif type_ == "tool.call":
            self._promote_pending()
            self.used_tool = True
            self.tools += 1
            if d.get("dangerous"):
                self._flush_seg()
                self.lines.append(danger_block(d))
            else:
                self.seg.add(d)
            self.live_text = ""
        elif type_ == "step.commit":
            self._promote_pending()
            digest = str(d.get("think_digest") or "").strip()
            if digest:
                self.lines.append(t("trace_think", digest=think_digest(digest)))
            self.pending_step = str(d.get("text") or "").strip()
            self.live_think = ""
        elif type_ == "reply.final":
            # 這一步的文字就是回覆本身，另外送出去了，不進軌跡
            self.pending_step = ""
            self.live_text = ""
        elif type_ == "status":
            self._feed_status(d)
        elif type_ == "bg.state":
            self.bg = list(d.get("tasks") or [])
        elif type_ == "turn.end":
            if not d.get("ok", True):
                self.status_cleared = True
        elif type_ == "error":
            if d.get("kind") == "STOPPED":
                self.stopped = True
            self.status_cleared = True

    def _feed_status(self, d: dict) -> None:
        self.status_cleared = False
        if "elapsed" in d:
            self.elapsed = float(d.get("elapsed") or 0.0)
        if d.get("model"):
            self.model = str(d["model"])
        if "effort" in d:
            self.effort = d.get("effort")
        if "ctx_tokens" in d:
            self.ctx_tokens = int(d.get("ctx_tokens") or 0)
        if "tools" in d:
            self.tools = int(d.get("tools") or 0)
        if "bg" in d:
            self.bg = list(d.get("bg") or [])
        # phase 沒帶＝一般回合；帶了才換階段。note 每則都以最新為準
        self.phase = str(d.get("phase") or "")
        self.note = str(d.get("note") or "")
        if d.get("resets_at"):
            self.resets_at = float(d["resets_at"])

    def _promote_pending(self) -> None:
        """上一步的文字後面又來了東西（工具或下一步）→ 它是動手前的說明，進軌跡。"""
        if self.pending_step:
            self.lines.append(self.pending_step)
            self.pending_step = ""

    def _flush_seg(self) -> None:
        """把進行中的一段收尾：統計行＋原文遮罩。空段什麼都不留。"""
        if self.seg.empty():
            self.seg = Seg()
            return
        summary = fmt_seg_summary(self.seg)
        if summary:
            self.lines.append(summary)
        out = spoiler_for(self.seg.raws)
        if out is not None:
            if out.line:
                self.lines.append(out.line)
            elif out.long:
                self.long_raws.append(out.long)
                self.lines.append(t("trace_detail_hint", n=len(out.long)))
        self.seg = Seg()

    def close_segment(self) -> None:
        """對外的收段入口：step.commit 之後、翻頁前、收工前呼叫。"""
        self._flush_seg()

    def take_long_raws(self) -> list[str]:
        out, self.long_raws = self.long_raws, []
        return out

    # ── 畫 ──
    def stopped_partial(self) -> str:
        """停止時已生成的半截回覆（清過標記）。"""
        return clean_reply(self.live_text)

    def trace_text(self) -> str:
        parts = [p for p in [self.header, *self.lines] if p]
        return "\n".join(parts)

    def has_trace(self) -> bool:
        return bool(self.lines) or not self.seg.empty()

    def _ctx_line(self) -> str:
        limit = 0
        try:
            limit = int(self.ctx_limit() or 0)
        except Exception:  # noqa: BLE001 — 純顯示，任何失敗都只是不畫百分比
            limit = 0
        if not limit or not self.ctx_tokens:
            return ""
        return t("st_ctx", pct=min(999, int(self.ctx_tokens * 100 / limit)))

    def status_block(self) -> str:
        if self.error_text:
            return self.error_text
        if self.status_cleared:
            return ""
        lines: list[str] = []
        if self.phase == "waiting":
            if self.resets_at:
                lines.append(t("st_waiting", ts=int(self.resets_at)))
            else:
                lines.append(t("st_waiting_note", note=self.note))
        elif self.phase == "compacting":
            lines.append(t("st_compacting"))
        else:
            step = int(self.elapsed // ELAPSED_STEP * ELAPSED_STEP)
            icon = _SPINNER[(step // ELAPSED_STEP) % len(_SPINNER)]
            model = (self.model or "").replace("claude-", "") or t("default_inline")
            lines.append(t(
                "st_line", icon=icon, label=t("st_label"), elapsed=step,
                model=model, effort=self.effort or t("default_inline"), ctx=self._ctx_line(),
            ))
            if self.note:
                lines.append(t("st_note", note=no_ticks(one_line(self.note))))
        seg_line = fmt_seg_summary(self.seg)
        if seg_line:
            lines.append(seg_line)
        running = [b for b in self.bg if b.get("status") == "running"]
        if running:
            desc = no_ticks(one_line(str(running[0].get("desc") or "")))[:60]
            lines.append(t("st_bg", desc=desc, n=len(running)))
        if self.live_text:
            tail = no_ticks(one_line(clean_reply(self.live_text)))[-LIVE_TAIL:]
            lines.append(t("st_generating", n=len(self.live_text), tail=tail))
        elif self.live_think:
            lines.append(t("st_think_tail", tail=no_ticks(one_line(self.live_think))[-THINK_TAIL:]))
        return "\n".join(lines)

    def render(self) -> str:
        body = self.trace_text()
        status = self.status_block()
        if body and status:
            return f"{body}\n\n{status}"
        return body or status
