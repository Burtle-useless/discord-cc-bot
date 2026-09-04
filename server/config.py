"""全域設定：環境清洗、監聽位址、逾時與模型常數。

本模組必須是整個服務**最先被 import** 的一個——模組載入時就執行環境變數清洗，
晚一步 engine 建起 CC 子進程就會繼承到污染的憑證設定。
"""
from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

# ── 環境變數清洗（必須在任何 CC 相關 import 之前）────────────────────────────
# 洗掉「上層 Claude session 洩漏的環境變數」：本服務多半是從桌面 app 的 CC 殼啟動的，
# 會繼承 ANTHROPIC_BASE_URL（指向 app 的本機代理）與 CLAUDE_CODE_* 等 session 變數，
# CC 子進程因此改走別人的短命憑證，token 一輪換就整批 401、重新登入也救不回
# （cc-bot 2026-07-21 實案，症狀完全不指向環境變數，卡了數小時）。
# 開機即清，CC 一律用本機 claude 登入憑證，不受啟動來源污染。
_LEAKED_KEYS: Final[tuple[str, ...]] = (
    "ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_AGENT_SDK_VERSION",
)
_purged: list[str] = [
    k for k in os.environ
    if k in _LEAKED_KEYS or k.startswith("CLAUDE_CODE_")
]
for _k in _purged:
    os.environ.pop(_k, None)

# 清洗會連 CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL 一起掃掉，於是子 session
# 完全沒有 AskUserQuestion 工具——助理遇到需要決定的岔路只能自己猜，App 端做好的
# 選項面板永遠不會出現。
# 這裡**明確設 1** 而不是把它加進清洗白名單：白名單只在「有繼承到」時有效，
# 而從桌面捷徑／工作排程啟動時這個變數根本不存在，放行等於沒放行。
os.environ["CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL"] = "1"

# ── 路徑 ─────────────────────────────────────────────────────────────────────
SERVER_DIR: Final[Path] = Path(__file__).resolve().parent


def _data_dir() -> Path:
    """資料目錄。正式服務用 `server/data`；測試與診斷腳本一律用暫存目錄。

    兩條路可以指到別處：`BUTLER_DATA_DIR` 明確指定；或者**執行的腳本本身在
    `tests/` 底下**就自動給一個暫存目錄。後者是硬擋不是禮貌——一支端對端測試
    曾經把假對話寫進正式的 session.json，而回合診斷檔裡有一半是測試紀錄。
    測試不該有辦法碰到正式資料。
    """
    override = os.environ.get("BUTLER_DATA_DIR")
    if override:
        return Path(override)
    entry = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if entry is not None and entry.parent.name == "tests":
        d = Path(tempfile.mkdtemp(prefix="butler-test-"))
        os.environ["BUTLER_DATA_DIR"] = str(d)      # 子進程也用同一個
        return d
    return SERVER_DIR / "data"


DATA_DIR: Final[Path] = _data_dir()
SESSION_FILE: Final[Path] = DATA_DIR / "session.json"
DEVICES_FILE: Final[Path] = DATA_DIR / "devices.json"
SCHEDULES_FILE: Final[Path] = DATA_DIR / "schedules.json"
DEFAULT_CWD: Final[Path] = Path(os.environ.get("BUTLER_CWD") or Path.home())
# dev_console.py 用哪一條對話；人格測試要用乾淨的新對話時從環境變數指定。
DEV_CONSOLE_CONV: Final[str] = os.environ.get("BUTLER_CONV") or "dev-console"


def claude_projects_dir() -> Path:
    """Claude Code 的逐字稿目錄：每個 session 一個 `<專案>/<session_id>.jsonl`。

    history／sessions／search／meta／local_usage 都要掃它，路徑只在這裡寫一次。
    做成函式而不是常數：測試用 `patch.object(Path, "home", …)` 把它指到暫存目錄，
    import 期就算死的話那個接縫就沒了。
    """
    return Path.home() / ".claude" / "projects"


def butler_token() -> str:
    """開發期固定 device token（非必要）。設了就優先於 devices.json 裡的註冊裝置，
    見 transport.auth.ensure_token。每次呼叫都重讀環境變數，理由同上。"""
    return (os.environ.get("BUTLER_TOKEN") or "").strip()


# 助理專屬的那條對話。App 的助理分頁固定看這一條，其餘 conv_id 都屬於工作區分頁。
# 兩邊是**不同用途**：助理有人格與生活工具（行事曆、記帳），工作區分頁純工作。
# options.py 依這個常數決定套哪一套 system prompt 與哪些自製工具。
PRIMARY_CONV: Final[str] = "main"

# ── 人格 ─────────────────────────────────────────────────────────────────────
# 檔名（不含副檔名），對應 server/personas/<名字>.txt。
# 語氣沒有標準答案，所以人格是**純文字檔**不是程式碼——改語氣不該需要碰 Python。
# 寫法見 docs/persona.md。
PERSONA: Final[str] = (os.environ.get("BUTLER_PERSONA") or "default").strip()
WORK_PERSONA: Final[str] = (os.environ.get("BUTLER_WORK_PERSONA") or "work").strip()

# 助理的「失憶自救」筆記檔。留空＝不啟用這條規則。
#
# 伺服器重啟會殺掉 CC 進程，resume 回來時最後一個回合接不回 context——
# 逐字稿裡有，模型腦子裡沒有。而失憶的人不知道自己失憶了，「重啟後」這個
# 條件抓不到，唯一可靠的信號是使用者說「你剛剛才查過」。
# 設了這個路徑，助理被這樣質疑時會先去讀檔案再回話。
NOTES_FILE: Final[str] = (os.environ.get("BUTLER_NOTES_FILE") or "").strip()

# CLI 的位置**交給 SDK 自己決定**，這裡只留人工覆寫的入口。
#
# 這裡曾經寫死 `%APPDATA%\npm\claude.cmd`。新版 claude-agent-sdk 拒絕 spawn 任何
# .bat/.cmd——Windows 用 cmd.exe 執行批次檔，參數可以被注入，而且沒有可靠的跳脫
# 方式——於是每個回合都在 connect() 拋 CLIConnectionError。
#
# **那個症狀完全不指向這裡**：伺服器正常、HTTP 200、訊息排進佇列，只有需要跑 CC
# 的路徑死掉，畫面上是 errors.py 的 UNKNOWN（「出了點狀況」）。舊版 SDK 沒有這道
# 檢查，所以同一行程式碼在此之前一直能跑：新裝的人必踩，既有的人升級 SDK 後才踩。
#
# 不改成寫死另一個路徑，是因為 SDK 的 wheel 自帶一支 `_bundled/claude.exe`，版本與
# SDK 同批發佈，讓它自己找就不會走鐘。要指定別的（例如測試特定版本）就設 CLAUDE_CLI
# 環境變數，**但必須指向原生 .exe**，不能是 npm 在 Windows 裝出來的 .cmd shim。
CLAUDE_CLI: Final[str | None] = os.environ.get("CLAUDE_CLI") or None

# ── 監聽位址（安全地基，見計畫風險 #7）──────────────────────────────────────
PORT: Final[int] = int(os.environ.get("BUTLER_PORT") or 47362)
# 開發旗標：**強制**綁 127.0.0.1。預設關閉，只在本機開發期手動開。
DEV_MODE: Final[bool] = (os.environ.get("BUTLER_DEV") or "0").strip() == "1"

# 明確指定監聽位址。留空＝自動找 tailnet 位址（預設，Tailscale 使用者不必設）。
#
# 存在的理由：Tailscale 只是**其中一種**把手機接進來的方式。走 Cloudflare Tunnel、
# ngrok、或自己架反向代理的人，服務該綁的是 127.0.0.1（代理從本機取件，外面
# 連同一個區網都掃不到這個 port）。寫死 tailnet 等於逼所有人裝 Tailscale。
#
# 這**不是**「綁哪裡都行」的開關：萬用位址一律拒絕，見 _check_bind。
BIND_HOST: Final[str] = (os.environ.get("BUTLER_BIND") or "").strip()

# Tailscale 用 CGNAT 網段 100.64.0.0/10 配發節點位址
_TAILNET: Final[ipaddress.IPv4Network] = ipaddress.ip_network("100.64.0.0/10")

# 萬用位址：綁上去等於對所有網路介面開放。引擎跑在 bypassPermissions 底下，
# 在公用 Wi-Fi 上這等於把整台電腦交出去，所以不管誰設的都拒絕。
_WILDCARD: Final[frozenset[str]] = frozenset({
    "0.0.0.0", "0", "::", "[::]", "::0", "*",
})


def _probe_local_ips() -> list[str]:
    """列出本機所有 IPv4 位址（不發封包，只查本機介面）。"""
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        return addrs
    except OSError:
        return []


def _probe_tailscale_cli() -> str | None:
    """備援：直接問 tailscale CLI。socket 查不到時（介面未註冊到 hostname）用這條。"""
    exe = os.path.expandvars(r"%ProgramFiles%\Tailscale\tailscale.exe")
    if not Path(exe).exists():
        return None
    try:
        out = subprocess.run(
            [exe, "ip", "-4"], capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    addr = out.stdout.strip().splitlines()
    return addr[0].strip() if addr else None


def find_tailnet_ip() -> str | None:
    """找出本機的 tailnet 位址，找不到回 None。"""
    for ip in _probe_local_ips():
        try:
            if ipaddress.ip_address(ip) in _TAILNET:
                return ip
        except ValueError:
            continue
    cli_ip = _probe_tailscale_cli()
    if cli_ip:
        try:
            if ipaddress.ip_address(cli_ip) in _TAILNET:
                return cli_ip
        except ValueError:
            pass
    return None


def _check_bind(host: str) -> str:
    """檢查手動指定的綁定位址，不通過就 raise。

    只擋萬用位址。區網位址（192.168.x.x 之類）放行但會在啟動時警告一句——
    那是使用者明講的選擇，不是誤設，但值得讓他知道同網段的人都連得到。
    """
    if host.lower() in _WILDCARD:
        raise RuntimeError(
            f"BUTLER_BIND={host} 是萬用位址，拒絕啟動。\n"
            "  → 引擎跑在 bypassPermissions 底下，打得到這個 port 就能對這台電腦\n"
            "     下任意指令。要從外面連進來請走通道（Cloudflare Tunnel 之類）\n"
            "     並把這裡設成 127.0.0.1，不要直接對外開。"
        )
    return host


def is_lan_bind(host: str) -> bool:
    """這個綁定位址會不會讓同網段的人連得到？只用來決定要不要印警告。"""
    if host.startswith("127.") or host == "localhost":
        return False
    try:
        return ipaddress.ip_address(host) not in _TAILNET
    except ValueError:
        return True    # 主機名解析不了就當成可能對外，寧可多警告一句


def resolve_bind_host() -> str:
    """決定 uvicorn 綁定位址。

    三條路，優先序由上而下：DEV_MODE → BUTLER_BIND → 自動找 tailnet。
    **永遠不回萬用位址**——引擎跑在 bypassPermissions 底下，任何能打到這個 port
    的人都能對這台電腦下任意指令。

    DEV_MODE **優先於**其餘兩者，不是「找不到 tailnet 時的退路」。這個旗標的用途是
    「隔離在本機驗證服務本身」，有裝 Tailscale 的人設了它卻照樣綁上 tailnet 的話，
    等於在不知情的狀況下把一個 bypassPermissions 的服務開給整個 tailnet 看得到。

    BUTLER_BIND 設了就照它走，**不再回頭找 tailnet**：同時裝了 Tailscale 又指定
    127.0.0.1 的人（走通道的常見組合）要的就是「不要綁上 tailnet」。
    """
    if DEV_MODE:
        return "127.0.0.1"
    if BIND_HOST:
        return _check_bind(BIND_HOST)
    ip = find_tailnet_ip()
    if ip:
        return ip
    raise RuntimeError(
        "找不到 Tailscale 位址（100.64.0.0/10），拒絕啟動。\n"
        "  → 用 Tailscale：確認它已安裝並登入（tailscale status）\n"
        "  → 用 Cloudflare Tunnel 之類的通道：設 BUTLER_BIND=127.0.0.1\n"
        "     （服務只綁本機，由通道從 loopback 取件送出去）\n"
        "  → 本機開發：設 BUTLER_DEV=1（只綁 127.0.0.1，手機連不到）"
    )


# ── 模型與引擎 ───────────────────────────────────────────────────────────────
DEFAULT_MODEL: Final[str] = os.environ.get("DEFAULT_MODEL") or "claude-sonnet-4-6"
# 訂閱方案：決定高階模型（Opus/Fable/Mythos）是否自動拿到 1M context。
# 填 max 或 team 才會要 1M；填錯或留空就走預設 context，不會壞掉只是拿不到。
ACCOUNT_PLAN: Final[str] = (os.environ.get("BUTLER_PLAN") or "").strip().lower()
FALLBACK_MODEL: Final[str] = "claude-sonnet-4-6"      # 主模型過載時的備援
DEFAULT_EFFORT: Final[str] = os.environ.get("DEFAULT_EFFORT") or "medium"
MAX_BUFFER_SIZE: Final[int] = 64 * 1024 * 1024        # stream-json 解析 buffer 上限

# client 進程池：cc-bot 用 8，本服務刻意壓到 3。
# 兩套引擎共用同一個 Claude 帳號，加起來的 Node 子進程數與 rate limit 都是共用的。
MAX_CLIENTS: Final[int] = 3
CLIENT_IDLE_TIMEOUT: Final[int] = 900                 # 閒置逾時：超過即回收該對話的 client
INACTIVITY_TIMEOUT: Final[int] = 600                  # CC 連續無輸出超過此秒數才視為卡死
# 有工具在跑（送出 ToolUseBlock、還沒收到它的 ToolResultBlock）期間的閒置上限。
# 一個沒輸出的工具（大檔下載、CC 自己的 Bash 上限也是 600 秒）滿 600 秒
# 就被當成卡死、整個 CLI 連同背景工作一起被殺——那不是卡死，是在等工具。
TOOL_INACTIVITY_TIMEOUT: Final[int] = 1800
# 判定逾時後先 interrupt() 讓 CLI 自己收尾，等這麼多秒收不了才真的殺進程。
INTERRUPT_GRACE_SEC: Final[float] = 5.0
MAX_EMPTY_RETRY: Final[int] = 3                       # 空回覆重試上限
MAX_AUTO_CONTINUE: Final[int] = 2                     # 未打完成標記時的自動續跑上限
# 這裡原本有 NOTIFY_AFTER_SEC=60（「跑超過這麼久才推播」）。已移除：短回合正是
# 人在外面最需要被通知的那種，而「該不該出聲」手機端判得更準。見 turn._turn_done。

# ── 安全 ─────────────────────────────────────────────────────────────────────
# 破壞性指令確認：cc-bot 預設關（人坐在電腦前看得到螢幕），
# 本服務預設**開**——使用情境是人在外面、電腦在家，沒有人在螢幕前把關。
CONFIRM_ENABLED: Final[bool] = (os.environ.get("CONFIRM_DANGEROUS") or "1").strip() == "1"
# 刻意小於 INACTIVITY_TIMEOUT，確認等待期間才不會被誤判成卡死
CONFIRM_TIMEOUT_SEC: Final[float] = 300.0

# ── 傳輸 ─────────────────────────────────────────────────────────────────────
# 全域共用一份（不是每裝置一份），所有對話的事件都排在同一條 ring 上。
# 從 2000 提到 6000 是 DELTA_COALESCE_MS 生效後的配套：先前 delta 實際上兩秒才送
# 一則，一個長回合幾百則就到頂；改成按時間窗送之後，同樣的回合會產生數倍事件量，
# 沿用 2000 會讓斷線續傳的視窗縮到只剩一兩分鐘。一則事件約數百 bytes，6000 則
# 也才幾 MB，用記憶體換續傳可靠度很划算。
RING_BUFFER_SIZE: Final[int] = 6000
SSE_KEEPALIVE_SEC: Final[int] = 15                    # 心跳間隔，防中間裝置掐斷閒置連線
# delta 合併窗。200ms＝每秒五次，人眼看起來已經是連續打字，
# 而事件量只有 100ms 的一半。這個值先前**定義了卻沒有人讀**，見 runner._DeltaBuffer。
DELTA_COALESCE_MS: Final[int] = 200


def startup_banner() -> str:
    """啟動摘要，讓「清洗了什麼、綁在哪裡」在日誌第一行就看得到。"""
    purged = f"已清洗 {len(_purged)} 個繼承變數" + (f"：{', '.join(_purged)}" if _purged else "")
    return f"[butler] {purged}"
