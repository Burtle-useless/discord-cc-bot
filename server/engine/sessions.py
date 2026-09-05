"""掃出這台電腦上所有 Claude Code 的 session，讓手機能挑一個接回去。

跟 `state.list_conversations()` 的差別：那邊只認 butler 自己開過的對話
（來源是 data/session.json），在別處用 CLI 或官方 App 跑的東西它一無所知。
這裡直接掃 `~/.claude/projects`，把電腦上所有 session 都撈出來。

兩個踩過的點寫在這裡免得下次重犯：

1. **專案目錄名不能拿來反推路徑。** CLI 把絕對路徑的每個非英數字元換成 `-`，
   `C:\\Users\\you\\Desktop\\我的專案` 會變成 `C--Users-you-Desktop-----`，
   非英數字元全被壓成一個 `-`，是不可逆的。真正的工作目錄要讀 jsonl 裡的 `cwd`。

2. **前幾行通常不是對話。** 檔頭常是 `queue-operation` 之類的維運記錄，
   沒有 `cwd` 也沒有 `message`，要往下掃到第一筆 `type=="user"` 才有料。
   另外實測 `type=="summary"` 一筆都沒有，別指望 CLI 給現成標題。
"""
from __future__ import annotations

import json
import os
import re
import uuid as _uuid
from pathlib import Path
from typing import Any

import config

from .history import _text_of
from .state import _load_map

# session 檔名就是 UUID。同目錄下還有 `<uuid>/tool-results/` 這種同名資料夾，
# 以及 memory/ 之類的非 session 目錄，靠這個格式一併擋掉。
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I,
)

# 找第一筆 user 記錄時最多往下讀幾行。正常檔案在前三行內就有，
# 讀到上限還沒有就當它沒有——不值得為了少數畸形檔把上百個檔全部讀完。
_HEAD_LINES = 40

# 標題長度上限。手機一行放不下太多，後面截掉。
_TITLE_MAX = 60

# 跟 history.load_history **同一份**黑名單：這些是維運產物，拿來當標題會變成
# 一整排「<command-name>」。先前這裡自己抄了一份而且少了四條（壓縮核對提示、
# <task-notification>、<system-reminder>、舊的背景工作回報），「接管電腦上的
# Claude」清單就會出現以它們為標題的 session。真相只留 history 那一份。
from .history import _OPS_PREFIXES as _NOISE_PREFIX  # noqa: E402


def _projects_dir() -> Path:
    # 留成函式是給測試換接縫用（patch.object(sessions, "_projects_dir", …)）
    return config.claude_projects_dir()


def _head_scan(jf: Path) -> dict[str, Any] | None:
    """掃一次檔頭，同時取回「能當標題的開場白」與「拿得到 cwd 的記錄」。

    兩件事合在同一次掃描：先前是 `_first_user_record` 找不到開場白就回 None，
    外層再叫 `_any_record_with_cwd` 把**同一個檔頭重開重讀一遍**——而找不到
    開場白正是「整個檔頭都是維運雜訊」的情況，也就是最常發生的那一種。

    回 `{"text": 開場白（沒有就空字串）, "rec": 用來讀 cwd 的記錄}`；
    整個檔頭什麼都撈不到才回 None。
    """
    fallback: dict[str, Any] | None = None      # 第一筆帶 cwd 的記錄，當退路
    entrypoint = ""                             # 這條 session 是誰開的（sdk-py／claude-desktop）
    try:
        with jf.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _HEAD_LINES:
                    break
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not entrypoint and r.get("entrypoint"):
                    entrypoint = str(r["entrypoint"])
                if fallback is None and r.get("cwd"):
                    fallback = r
                if r.get("type") != "user" or r.get("isCompactSummary"):
                    continue
                text = _text_of((r.get("message") or {}).get("content")).strip()
                if not text or text.startswith(_NOISE_PREFIX):
                    # 有雜訊但仍拿得到 cwd，先記著，繼續往下找像樣的開場白
                    continue
                return {"text": text, "rec": r,
                        "entrypoint": entrypoint or str(r.get("entrypoint") or "")}
    except OSError:
        return None
    return {"text": "", "rec": fallback, "entrypoint": entrypoint} if fallback else None


# 本專案的前端（手機 App／Discord bot）發訊時蓋在第一則訊息上的時間戳，
# 例如 `[09/05 週六 11:34 手機] …`。有這個＝人真的打過字
# （turn.stamp 只蓋在真實使用者訊息上）。
_STAMP_RE = re.compile(r"^\[\d{2}/\d{2} 週. \d{2}:\d{2}")


def _user_initiated(entrypoint: str, first_text: str) -> bool:
    """這條 session 是不是「由人發起」。

    清單要的是「人真的講過話」的對話；混進來的雜訊全是程式自己開的 SDK
    client——模型探測、標題生成、一次性測試——它們的 entrypoint 一律是
    `sdk-py`，而人經由本專案前端講的第一句話一定帶 stamp 前綴。所以：
    **非 SDK 一律當人**（官方桌面 App 是 claude-desktop，終端 CLI 若有
    別的值也一樣放行），**SDK 的要驗前綴**。

    代價說在前面：比 stamp 機制更早的舊對話沒有前綴，會一併被濾掉——
    要接舊對話的機率遠低於每天被測試雜訊淹沒的成本。
    """
    if entrypoint and entrypoint != "sdk-py":
        return True
    return bool(_STAMP_RE.match(first_text))


def _any_record_with_cwd(jf: Path) -> dict[str, Any] | None:
    """退而求其次：只要拿到帶 cwd 的記錄就好，標題留空。"""
    try:
        with jf.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _HEAD_LINES:
                    break
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("cwd"):
                    return r
    except OSError:
        return None
    return None


def scan_sessions(limit: int = 60, q: str = "", offset: int = 0) -> list[dict]:
    """列出電腦上的 session，最近用過的排前面。

    [limit] 是回傳筆數上限；[offset] 是要跳過的前幾筆（分頁用）；
    [q] 有值時對標題與工作目錄做不分大小寫的子字串比對。

    offset 不能直接切 cands：taken 與「讀不出內容」的檔會在迴圈裡才被濾掉，
    照檔案序跳前 N 個會漏掉或重複。所以是數「已通過過濾的筆數」來跳。

    butler 自己已經接管的 session 會被濾掉——那些在對話清單裡本來就看得到，
    重複列出只會讓人分不清該點哪一個。
    """
    root = _projects_dir()
    if not root.is_dir():
        return []

    # 兩個 id 都要算「已接管」：接管接的是複印本，所以對話存的 session_id 是
    # 複印本的，原始那筆要靠 forked_from 才認得出來——只比對前者的話，剛接管過
    # 的 session 會立刻又出現在清單上，看起來像沒接成功。
    taken: set[str] = set()
    for rec in _load_map().values():
        for key in ("session_id", "forked_from"):
            if rec.get(key):
                taken.add(rec[key])

    # 先只看檔案屬性排序，不開檔——上百個檔全部解析要幾秒，而使用者只看得到前幾十筆
    cands: list[tuple[float, Path]] = []
    for jf in root.glob("*/*.jsonl"):
        if not jf.is_file() or not _UUID_RE.match(jf.stem) or jf.stem in taken:
            continue
        try:
            stat = jf.stat()
        except OSError:
            continue
        if stat.st_size == 0:
            continue
        cands.append((stat.st_mtime, jf))
    cands.sort(key=lambda e: e[0], reverse=True)

    # 有搜尋字串時要多翻幾頁才湊得滿，沒有的話多讀就是白工。
    # 第二頁以後要連前面幾頁一起重掃（無狀態分頁），所以額度算的是 offset+limit。
    # 倍率 5 不是 2：來源過濾會把 SDK 測試雜訊整批丟掉，
    # 倍率太小會湊不滿一頁還以為到底了。
    budget = len(cands) if q else min(len(cands), (offset + limit) * 5)
    needle = q.strip().lower()

    out: list[dict] = []
    matched = 0
    for mtime, jf in cands[:budget]:
        hit = _head_scan(jf)
        rec = hit["rec"] if hit else None
        if rec is None:
            continue
        # 程式自己開的 session（探測、標題生成、測試）不列——見 _user_initiated
        if not _user_initiated(hit.get("entrypoint", ""), hit["text"]):
            continue
        cwd = str(rec.get("cwd") or "")
        title = " ".join(hit["text"].split())[:_TITLE_MAX]
        if needle and needle not in title.lower() and needle not in cwd.lower():
            continue
        matched += 1
        if matched <= offset:
            continue
        out.append({
            "session_id": jf.stem,
            "cwd": cwd,
            "title": title or "（沒有開場白）",
            "mtime": mtime,
            "branch": str(rec.get("gitBranch") or ""),
            # subagent 產生的 session 也在同一棵樹裡，標出來讓使用者自己判斷
            "sidechain": bool(rec.get("isSidechain")),
        })
        if len(out) >= limit:
            break
    return out


def session_text(session_id: str, max_chars: int = 3000, keep: str = "head") -> str:
    """把一個 session 的對話讀成一整段純文字（給模型看的，不是給人看的）。

    `keep="head"` 從頭累積到上限就停（取標題用）；`keep="both"` 讀完整段之後取
    頭尾各半、中間以 `[...]` 接起——交接稿要的正是這兩端：開頭是「原本要做什麼」，
    結尾是「現在做到哪」，中間的過程反而最不重要。

    走 `history.load_history` 而不是自己開 jsonl：那邊已經濾掉壓縮摘要與各種維運
    注入（續跑提示、task-notification、skill 全文），不濾的話交接稿會被那些東西灌滿。
    """
    # 直接照 session_id 找檔，不經過對話登記表：`/search` 要索引的正是那些
    # **還沒被接管**的 session，它們不在登記表裡（先前寫成查表，那條路整個是空的）。
    from .history import _parse_lines, _session_file

    jf = _session_file(session_id)
    if jf is None:
        return ""
    try:
        with jf.open(encoding="utf-8", errors="replace") as f:
            msgs = _parse_lines(f, 200 if keep == "head" else 0)
    except OSError:
        return ""
    parts = [f"{m.get('role', '')}: {m['text']}" for m in msgs if m.get("text")]
    if not parts:
        return ""
    text = "\n".join(parts)
    if len(text) <= max_chars:
        return text
    if keep == "head":
        return text[:max_chars]
    half = max_chars // 2
    return text[:half] + "\n\n[...]\n\n" + text[-half:]


def session_cwd(session_id: str) -> str:
    """單獨問一個 session 的工作目錄。接管時要用它把對話的 cwd 設對。"""
    for jf in _projects_dir().glob(f"*/{session_id}.jsonl"):
        rec = _any_record_with_cwd(jf)
        return str((rec or {}).get("cwd") or "")
    return ""


def fork_session(session_id: str) -> str | None:
    """把一個 session 複印成獨立的新 session，回新的 id；找不到原檔回 None。

    **接管一定要複印，不能沿用同一個 id。** session 的逐字稿是一個
    append-only 的 jsonl，而 CC 對它沒有任何跨行程的鎖：兩邊各自 `--resume`
    同一個 id，就會各自把整份讀進去建 context、各自往尾巴接，誰也不知道對方
    寫了什麼。

    這不是假想的風險，是量到的現況。2026-08-18 檢查一份被同時使用的逐字稿：
    29507 筆記錄裡有 5294 個分叉點（同一個 parentUuid 長出多個子節點），
    而且分叉兩邊的 `version` 不同——24837 筆 2.1.216（桌面 App）對 1905 筆
    2.1.181（butler 起的 npm 版 claude.exe），最早可以追到 8/10。版本號不會
    因為重試或改訊息而變，那只可能是兩個不同的執行檔在寫同一個檔案。

    複印之後兩邊就各走各的：助理拿到的是接管當下的完整歷史快照，之後桌面那邊
    再聊什麼都不會流進來，反之亦然。代價是佔一份磁碟，以及兩邊不再同步——
    但「不同步」本來就是唯一誠實的語意，共用一份檔案給出的是「看起來同步、
    實際上互相覆蓋」。

    `sessionId` 逐筆改寫而不是整檔字串取代：這個欄位的字面樣子
    （`"sessionId":"<uuid>"`）完全可能出現在對話內容裡——光是討論這個 bug
    的那幾輪就寫過好幾次——字串取代會連人家講的話一起改掉。
    """
    src = next(iter(_projects_dir().glob(f"*/{session_id}.jsonl")), None)
    if src is None:
        return None

    new_id = str(_uuid.uuid4())
    dst = src.with_name(f"{new_id}.jsonl")
    tmp = src.with_name(f"{new_id}.jsonl.tmp")

    # 先寫暫存再改名：逐字稿可以到上百 MB，中途失敗會留下半份檔案，
    # 而半份 jsonl 看起來跟正常的一樣（就是行數少），CC 會安靜地少一段歷史。
    try:
        with src.open(encoding="utf-8", errors="replace") as fin, \
                tmp.open("w", encoding="utf-8", newline="\n") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    # 壞行照抄。丟掉會讓 uuid 鏈斷掉，那比留著一行讀不懂的更糟。
                    fout.write(line + "\n")
                    continue
                if isinstance(rec, dict) and rec.get("sessionId"):
                    rec["sessionId"] = new_id
                # 緊湊分隔符：CC 自己寫出來就是沒有多餘空白的，預設的 ", " / ": "
                # 會讓一份上百 MB 的逐字稿平白多出幾百 KB，格式也跟原檔不一致。
                fout.write(
                    json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n",
                )
        tmp.replace(dst)
        # 把時間戳改回跟原檔一樣，**這步不能省**。複印本剛寫出來時 mtime 是
        # 「現在」，在 ~/.claude/projects 裡就成了最新的一筆，於是桌面那邊下次
        # `claude --continue` 會挑中它——2026-08-18 真的發生了：複印本 18:57
        # 生出來，桌面 CC 一重啟就接到複印本上，butler 的對話也還指著同一個
        # 檔，雙寫原封不動地換個檔名回來了。
        # 複印本是歷史快照，不是剛用過的對話，時間戳照著原檔才誠實。
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    return new_id
