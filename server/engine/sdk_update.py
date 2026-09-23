"""Claude Agent SDK（連同它自帶的 Claude Code CLI）的版本資訊與一鍵更新。

**為什麼要能從手機更新。** 官方發新模型時，後端會直接擋掉太舊的 CLI：選了新模型
就回 400「Claude Code <版本> does not support this model; version <版本> or newer is
required」（例：SDK 0.2.152 帶的 CLI 2.1.259 選 Opus 5.5，被要求 2.1.280 以上）。
模型清單可以自己長（見 models），但 CLI 不會自己升級：SDK 把 CLI 打包在 wheel 裡，
不像官方終端機會自動更新。沒有這個功能的話，每次都得在電腦前手動做一遍下面這套流程。

**更新流程**（`_run`）：下載 wheel → 備份目前的套件 → 把正在跑的 `claude.exe` 改名讓位
→ pip 安裝 → 用新版實際跑一句話 → 通過才算數，任何一步失敗就退回舊版。

改名讓位是必要的：這個服務（以及共用同一個環境的其他服務）正開著
`_bundled/claude.exe`，Windows 不准覆寫執行中的檔案，pip 會半途失敗留下半套，
整個環境就跟著壞掉。改名不受執行鎖影響，舊行程用的是已經開啟的控制代碼，照跑不誤。
舊檔等重啟後才刪得掉（`cleanup_parked`）。

更新完一定要重啟：已經載入記憶體的 SDK 還是舊版。重啟由呼叫端（system_api）處理。
"""
from __future__ import annotations

import importlib.metadata
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config

log = logging.getLogger(__name__)

DIST = "claude-agent-sdk"
PYPI_URL = "https://pypi.org/pypi/claude-agent-sdk/json"

# PyPI 最新版快取多久。SDK 幾天發一版，一天問幾次綽綽有餘；失敗時隔短一點再試。
LATEST_CACHE_SEC = 6 * 3600
LATEST_FAIL_SEC = 600

WORK_DIR = config.DATA_DIR / "sdk_update"

# pip 下載 wheel（約 100 MB）與安裝的時限
DOWNLOAD_TIMEOUT_SEC = 900
INSTALL_TIMEOUT_SEC = 600
SMOKE_TIMEOUT_SEC = 180

_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")

# 背景跑的子行程不要冒黑窗（伺服器本身是隱藏跑的，子行程會自己開一個看得見的）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 新版裝好之後用它實際跑一輪：import 得到、版本對、CLI 起得來、真的能回話。
# Haiku、沒有工具、不讀使用者設定、不寫逐字稿——只驗「這一套能用」，不留痕跡。
_SMOKE = r"""
import asyncio, sys
import claude_agent_sdk as sdk
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
if sdk.__version__ != sys.argv[1]:
    raise SystemExit("版本不對：" + sdk.__version__)
async def main():
    opts = ClaudeAgentOptions(model="haiku", tools=[], max_turns=1, strict_mcp_config=True,
                              setting_sources=[], extra_args={"no-session-persistence": None})
    async with ClaudeSDKClient(opts) as c:
        await c.query("只回覆 OK")
        async for m in c.receive_response():
            if isinstance(m, ResultMessage):
                if m.is_error:
                    raise SystemExit("回合出錯：" + (m.result or "")[:200])
                print("SMOKE_OK", flush=True)
                return
asyncio.run(main())
"""


# ── 版本資訊 ──────────────────────────────────────────────────────────────────

def package_dir() -> Path:
    import claude_agent_sdk
    return Path(claude_agent_sdk.__file__).parent


def installed_sdk() -> str:
    """磁碟上裝的 SDK 版本（不是記憶體裡載入的那份——更新後、重啟前兩者會不同）。"""
    try:
        return importlib.metadata.version(DIST)
    except importlib.metadata.PackageNotFoundError:
        return ""


def cli_path() -> Path:
    """實際會被叫起來的 CLI：有設 CLAUDE_CLI 就是它，否則是 SDK 自帶的
    （Windows 上叫 claude.exe，其他平台叫 claude，跟 SDK 自己找的規則一樣）。"""
    if config.CLAUDE_CLI:
        return Path(config.CLAUDE_CLI)
    return package_dir() / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")


_cli_cache: dict[str, str] = {}


def cli_version() -> str:
    """CLI 的版本（`2.1.280`）。問一次 `--version` 就記住：同一個行程裡它不會變，
    更新之後一定會重啟。拿不到回空字串。"""
    exe = cli_path()
    key = str(exe)
    if key not in _cli_cache:
        try:
            out = subprocess.run([str(exe), "--version"], capture_output=True, text=True,
                                 timeout=30, creationflags=_NO_WINDOW)
            m = _VERSION_RE.search(out.stdout or "")
            _cli_cache[key] = m.group(0) if m else ""
        except (OSError, subprocess.SubprocessError):
            return ""
    return _cli_cache[key]


def cli_version_cached() -> str:
    """問過才有；還沒問過回空字串，不開子行程（給不能等的地方用）。"""
    try:
        return _cli_cache.get(str(cli_path()), "")
    except Exception:  # noqa: BLE001
        return ""


_latest: dict[str, object] = {}


def _vkey(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in _VERSION_RE.search(v or "0").group(0).split("."))  # type: ignore[union-attr]


def newer(a: str, b: str) -> bool:
    """a 是不是比 b 新。認不得的版本字串一律當不新（寧可不提示，也不要亂提示）。"""
    try:
        return _vkey(a) > _vkey(b)
    except (AttributeError, ValueError):
        return False


def latest() -> dict[str, str] | None:
    """PyPI 上最新的 SDK 版本與發布日（`{"version": "0.2.158", "released": "YYYY-MM-DD"}`）。

    有快取；失敗回 None 並短暫記住，免得斷網時每次開工具頁都等一輪逾時。
    只看正式版，不看預覽版。"""
    now = time.time()
    if "data" in _latest and now - float(_latest["ts"]) < LATEST_CACHE_SEC:  # type: ignore[arg-type]
        return _latest["data"]  # type: ignore[return-value]
    if now - float(_latest.get("fail_ts", 0)) < LATEST_FAIL_SEC:  # type: ignore[arg-type]
        return None
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=10) as r:
            d = json.loads(r.read())
        ver = str(d["info"]["version"])
        files = d.get("releases", {}).get(ver) or []
        released = str(files[0].get("upload_time", ""))[:10] if files else ""
        data = {"version": ver, "released": released}
    except Exception:  # noqa: BLE001
        _latest["fail_ts"] = now
        return None
    _latest.update(data=data, ts=now)
    _latest.pop("fail_ts", None)
    return data


# ── 更新工作 ──────────────────────────────────────────────────────────────────

@dataclass
class Job:
    state: str = "idle"          # idle / running / done / manual（裝好了但沒重啟成）/ failed
    step: str = ""               # 給人看的目前步驟
    target: str = ""
    error: str = ""
    finished_at: float = 0.0


_job = Job()
_lock = threading.Lock()


def job() -> Job:
    return _job


def running() -> bool:
    return _job.state == "running"


def start(target: str, on_done: Callable[[], None]) -> bool:
    """在背景執行緒開始更新到 `target`。已經有一個在跑就回 False。

    `on_done` 在新版驗證通過後呼叫（重啟由它負責，重啟不了就拋例外，工作會標成
    manual）；失敗時不呼叫，舊版照常跑。"""
    global _job
    with _lock:
        if _job.state == "running":
            return False
        _job = Job(state="running", step="準備中", target=target)
    threading.Thread(target=_run_guarded, args=(target, on_done), daemon=True,
                     name="sdk-update").start()
    return True


def _run_guarded(target: str, on_done: Callable[[], None]) -> None:
    try:
        _run(target)
    except Exception as e:  # noqa: BLE001 — 任何失敗都要落到 job 上讓手機看得到
        log.exception("SDK 更新失敗")
        _job.state, _job.error = "failed", str(e)[:300]
        _job.finished_at = time.time()
        return
    _job.state, _job.step = "done", "完成，重新啟動中"
    _job.finished_at = time.time()
    try:
        on_done()
    except Exception:  # noqa: BLE001
        log.exception("SDK 更新完成但重啟失敗")
        # 新版已經裝好，只是沒重啟成。停在 done 的話手機會一直等一個不會發生的重啟
        _job.state, _job.step = "manual", "新版已裝好，要手動重啟服務才會生效"


def _step(text: str) -> None:
    _job.step = text
    log.info("SDK 更新：%s", text)


def _pip(args: list[str], timeout: float) -> None:
    p = subprocess.run([sys.executable, "-m", "pip", *args, "--disable-pip-version-check"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=_NO_WINDOW)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError("pip " + args[0] + " 失敗：" + " / ".join(tail))


def park(exe: Path, tag: str) -> Path | None:
    """把執行中的 exe 改名讓位，回新名字；檔案不在就回 None。"""
    if not exe.exists():
        return None
    for i in range(100):
        dst = exe.with_name(f"claude-{tag or 'old'}{'' if i == 0 else f'-{i}'}.old.exe")
        if not dst.exists():
            exe.rename(dst)
            return dst
    raise RuntimeError("找不到可用的讓位檔名")


def cleanup_parked() -> int:
    """刪掉之前更新時改名讓位的舊 exe。還有行程開著的刪不掉，留到下次。回刪了幾個。"""
    try:
        bundled = package_dir() / "_bundled"
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for f in bundled.glob("claude-*.old.exe"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def _run(target: str) -> None:
    pkg = package_dir()
    site = pkg.parent
    old = installed_sdk()
    old_cli = cli_version()
    if not newer(target, old):
        raise RuntimeError(f"目前已是 {old}，不用更新")

    _step("下載新版")
    wheels = WORK_DIR / "wheel"
    shutil.rmtree(wheels, ignore_errors=True)
    wheels.mkdir(parents=True, exist_ok=True)
    _pip(["download", f"{DIST}=={target}", "--no-deps", "--only-binary=:all:",
          "-d", str(wheels)], DOWNLOAD_TIMEOUT_SEC)
    wheel = next(wheels.glob("claude_agent_sdk-*.whl"), None)
    if wheel is None:
        raise RuntimeError("下載完找不到 wheel 檔")

    _step("備份目前版本")
    backup = WORK_DIR / f"backup-{old}"
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(pkg, backup / pkg.name,
                    ignore=shutil.ignore_patterns("claude*.exe", "__pycache__"))
    dist = site / f"claude_agent_sdk-{old}.dist-info"
    if dist.is_dir():
        shutil.copytree(dist, backup / dist.name)

    _step("讓出執行中的 CLI")
    exe = pkg / "_bundled" / "claude.exe"
    parked = park(exe, old_cli)

    try:
        _step("安裝新版")
        _pip(["install", "--no-deps", "--no-input", str(wheel)], INSTALL_TIMEOUT_SEC)
        _step("試跑新版")
        p = subprocess.run([sys.executable, "-c", _SMOKE, target], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=SMOKE_TIMEOUT_SEC, cwd=str(WORK_DIR),
                           creationflags=_NO_WINDOW)
        if p.returncode != 0 or "SMOKE_OK" not in (p.stdout or ""):
            tail = (p.stderr or p.stdout or "").strip().splitlines()[-2:]
            raise RuntimeError("新版試跑沒過：" + " / ".join(tail))
    except Exception:
        _step("失敗，退回舊版")
        _rollback(pkg, backup, parked)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(wheels, ignore_errors=True)


def _rollback(pkg: Path, backup: Path, parked: Path | None) -> None:
    """解除新版、把備份放回去、讓位的 exe 改回原名。盡力而為，每一步都不讓例外蓋掉原因。"""
    try:
        _pip(["uninstall", "-y", DIST], INSTALL_TIMEOUT_SEC)
    except Exception:  # noqa: BLE001
        log.exception("退回：解除新版失敗")
    try:
        for d in backup.iterdir():
            shutil.copytree(d, pkg.parent / d.name, dirs_exist_ok=True)
    except Exception:  # noqa: BLE001
        log.exception("退回：放回備份失敗")
    if parked is not None:
        exe = pkg / "_bundled" / "claude.exe"
        try:
            if exe.exists():
                exe.unlink()
            parked.rename(exe)
        except OSError:
            log.exception("退回：CLI 改回原名失敗")
