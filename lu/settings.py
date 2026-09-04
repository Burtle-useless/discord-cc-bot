"""陸自己的設定：讀 `.env`，整理成一個 frozen dataclass。

**這個模組刻意不叫 `config.py`**：引擎的 `server/config.py` 會被加進 sys.path，
同名會撞。也刻意**不 import 任何 engine 的東西**——它得在 `bootstrap.prepare()`
設好環境變數之前就能用。

讀哪些環境變數（全部在這個 repo 根目錄的 `.env`）：
  DISCORD_TOKEN      Discord bot token。`--check` 乾跑可以沒有。
  ALLOWED_USER       主帳號的 Discord user id（必填）。
  BUTLER_SERVER_DIR  引擎目錄。選填，預設就是這個 repo 自帶的 `server\\`。
  UPDATE_CHANNEL     更新公告頻道 id（選填，0＝不推）。
  BOT_LANG           介面語言 zh-TW／en（預設 zh-TW）。
  CONFIRM_DANGEROUS  破壞性指令先問（1）或直接跑（0，預設）。
  BUTLER_CWD         新對話的預設工作目錄（舊名 DEFAULT_DIR 也認；預設家目錄）。
  SHARE_SCRIPT／SHARE_HOURS  超過上傳上限的檔案改走臨時連結（選填，見 lu/files.py）。
  SIDEBAR_CATEGORY／SIDEBAR_ENTRY  側欄分類與入口頻道名（選填，預設走 i18n）。
  LU_DATA_DIR        資料目錄（預設本 repo 的 `data\\`；測試指到暫存目錄）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# cc-bot 目錄（lu/ 的上一層）。資料目錄、.env、舊狀態檔都以它為基準。
BOT_DIR: Path = Path(__file__).resolve().parent.parent

# 引擎（回合邏輯：排隊、插話、限流續跑、壓縮、背景工作）就在這個 repo 的 `server/` 底下，
# clone 下來就有，不必另外裝任何東西。`BUTLER_SERVER_DIR` 只是給想指到別份引擎的人用的覆寫。
DEFAULT_SERVER_DIR: Path = BOT_DIR / "server"
BROKEN_SERVER_MSG = (
    "找不到引擎：{path} 底下沒有 engine 目錄。\n"
    "正常情況下它就在這個 repo 的 server\\ 裡，clone 完就該存在——\n"
    "如果是自己刪掉或只抓了部分檔案，重新 clone 一次最快。\n"
    "（想指到別份引擎可以用環境變數 BUTLER_SERVER_DIR 覆寫。）"
)

# 單一實例鎖用的 port。跟舊 discord_bot.py 同一個，watchdog／重啟腳本拿它當活著的判準，
# 換了號碼那幾支腳本全部要改。
LOCK_PORT: int = 47361

# 這幾個是**引擎**（`server/config.py`）在 import 當下讀的環境變數，陸自己不用。
# `.env` 的值不會自己進 os.environ，所以要原樣收下、由 `bootstrap.prepare()` 轉過去；
# 少了這一步，在 .env 裡寫 DEFAULT_MODEL 會完全沒有反應且不報錯。
# （BUTLER_PORT／BIND／TOKEN 那些是手機 App 伺服器專用的，Discord 這邊沒有意義，不收。）
ENGINE_ENV_KEYS: tuple[str, ...] = (
    "CLAUDE_CLI", "DEFAULT_MODEL", "DEFAULT_EFFORT", "BUTLER_PLAN", "BUTLER_PERSONA",
)


@dataclass(frozen=True, slots=True)
class Settings:
    """陸的全部設定。欄位一旦建好就不變；要改就重建一個。"""

    token: str | None                 # 沒有 token 只能乾跑（--check），不能上線
    owner_id: int                     # 主帳號
    update_channel: int               # 0＝停用
    lang: str                         # zh-TW / en
    confirm_dangerous: bool
    default_cwd: Path
    data_dir: Path
    butler_server: Path
    sidebar_category: str | None      # None＝用 i18n 的預設名
    sidebar_entry: str | None
    share_script: str                 # ""＝停用大檔臨時連結（見 lu/files.py）
    share_hours: int
    engine_env: dict[str, str]        # 原樣轉給引擎的環境變數（見 ENGINE_ENV_KEYS）

    @property
    def env_file(self) -> Path:
        return BOT_DIR / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    """極簡 .env 解析：`KEY=VALUE` 一行一個，`#` 開頭是註解，值兩側的引號去掉。

    不用 python-dotenv 的 load_dotenv：它會直接寫進 os.environ，測試要載一份假的
    .env 時就污染了整個進程。這裡只回一個 dict，要不要放進環境由呼叫端決定。
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def _to_int(val: str | None, default: int = 0) -> int:
    try:
        return int((val or "").strip() or default)
    except ValueError:
        return default


def load(env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
    """讀設定：`.env` 的值墊底，真正的環境變數蓋過它（跟 load_dotenv 預設一樣的優先序）。

    [env_file] 預設是本 repo 的 `.env`；[environ] 預設 os.environ（測試可以塞假的）。
    兩個必填的缺一個就直接炸：沒有主帳號的 bot 誰都不能用；沒有引擎的話連一則
    訊息都跑不了。現在炸的訊息看得懂，晚一步就是一句 `ModuleNotFoundError: engine`。
    """
    env = dict(_read_env_file(env_file if env_file is not None else BOT_DIR / ".env"))
    env.update(environ if environ is not None else os.environ)

    owner_raw = (env.get("ALLOWED_USER") or "").strip()
    if not owner_raw.isdigit():
        raise RuntimeError("缺少必要環境變數 ALLOWED_USER（主帳號的 Discord user id），請在 .env 填上")

    token = (env.get("DISCORD_TOKEN") or "").strip() or None
    lang = (env.get("BOT_LANG") or "zh-TW").strip() or "zh-TW"
    cwd_raw = (env.get("BUTLER_CWD") or env.get("DEFAULT_DIR") or "").strip()
    default_cwd = Path(cwd_raw) if cwd_raw else Path.home()
    if not default_cwd.is_dir():
        default_cwd = Path.home()
    data_raw = (env.get("LU_DATA_DIR") or "").strip()
    data_dir = Path(data_raw) if data_raw else BOT_DIR / "data"

    server_raw = (env.get("BUTLER_SERVER_DIR") or "").strip()
    butler_server = Path(server_raw).expanduser() if server_raw else DEFAULT_SERVER_DIR
    # 引擎不在就現在講清楚，不要等第一則訊息才炸 ImportError——那時候訊息裡
    # 完全看不出真正缺的是什麼。
    if not (butler_server / "engine").is_dir():
        raise RuntimeError(BROKEN_SERVER_MSG.format(path=butler_server))

    return Settings(
        token=token,
        owner_id=int(owner_raw),
        update_channel=_to_int(env.get("UPDATE_CHANNEL"), 0),
        lang=lang,
        confirm_dangerous=(env.get("CONFIRM_DANGEROUS") or "0").strip() == "1",
        default_cwd=default_cwd,
        data_dir=data_dir,
        butler_server=butler_server,
        sidebar_category=(env.get("SIDEBAR_CATEGORY") or "").strip() or None,
        sidebar_entry=(env.get("SIDEBAR_ENTRY") or "").strip() or None,
        share_script=(env.get("SHARE_SCRIPT") or "").strip(),
        share_hours=_to_int(env.get("SHARE_HOURS"), 24) or 24,
        engine_env={
            k: (env.get(k) or "").strip()
            for k in ENGINE_ENV_KEYS if (env.get(k) or "").strip()
        },
    )
