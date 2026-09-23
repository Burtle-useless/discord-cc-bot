"""CLI 行程樹的善後（Windows）。

為什麼需要：實測服務底下會殘留好幾天前的 claude.exe，各帶一整棵 MCP 樹（node、python、
各種 *-mcp），幾十個行程閒置好幾天——連線池早就把它們移除了（/v1/health 回報
clients=0），行程卻沒結束。

兩個成因（都查過 SDK 0.2.152 的 `subprocess_cli.close()`）：
1. 它的 terminate／kill 升級在呼叫端正被 asyncio 取消時會被跳過，它自己的 docstring
   寫明了。按「停止」時，drop 正好跑在被取消的回合收尾裡。
2. Windows 的 terminate() 只殺 claude.exe 本身，它底下的 MCP 伺服器不會跟著死。

做法：關閉**前**先記下 CLI 的整棵子行程（pid＋建立時間），關閉後還活著的，只要是
「輔助行程」（CLI、node、python、shell、名字帶 mcp 的）就收掉。**其他程式一律不碰**：
殺整棵樹的做法會連帶殺掉從同一個終端機開出來的瀏覽器、常駐工具等無關程式。
建立時間用來防 pid 重用：記下來的 pid 已經換成別的行程的話，絕不動它。

只用 kernel32（ctypes），不另裝套件。非 Windows 一律 no-op。
"""
from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Proc:
    pid: int
    ppid: int
    name: str
    created: int = 0     # 建立時間（FILETIME 的 100ns 刻度）；0＝拿不到，不做 pid 重用檢查


# 可以收的輔助行程。MCP 伺服器的執行檔名五花八門（weather-mcp.exe、notes-mcp.exe…），
# 名字帶 mcp 的一律算；其餘是 CLI 本身與它拿來跑 MCP 的直譯器與 shell。
HELPER_NAMES: frozenset[str] = frozenset({
    "claude.exe", "node.exe", "python.exe", "pythonw.exe", "uv.exe", "uvx.exe",
    "npx.exe", "bun.exe", "deno.exe",
    "cmd.exe", "conhost.exe", "powershell.exe", "pwsh.exe", "bash.exe", "sh.exe",
})


def is_helper(name: str) -> bool:
    n = name.lower()
    return n in HELPER_NAMES or "mcp" in n


_WIN = sys.platform == "win32"

if _WIN:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _TH32CS_SNAPPROCESS = 0x00000002
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.restype = wintypes.BOOL
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.Process32NextW.restype = wintypes.BOOL
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _k32.GetExitCodeProcess.restype = wintypes.BOOL
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


def snapshot() -> list[tuple[int, int, str]]:
    """目前所有行程的 (pid, 父 pid, 執行檔名)。非 Windows 回空清單。"""
    if not _WIN:
        return []
    h = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not h or h == _INVALID_HANDLE:
        return []
    out: list[tuple[int, int, str]] = []
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _k32.Process32FirstW(h, ctypes.byref(e))
        while ok:
            out.append((int(e.th32ProcessID), int(e.th32ParentProcessID), str(e.szExeFile)))
            ok = _k32.Process32NextW(h, ctypes.byref(e))
    finally:
        _k32.CloseHandle(h)
    return out


def _created(pid: int) -> int:
    """行程的建立時間；拿不到（已結束、權限不足）回 0。"""
    if not _WIN:
        return 0
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return 0
    try:
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        if not _k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                    ctypes.byref(k), ctypes.byref(u)):
            return 0
        return (int(c.dwHighDateTime) << 32) | int(c.dwLowDateTime)
    finally:
        _k32.CloseHandle(h)


def descendants(root_pid: int, procs: list[tuple[int, int, str]] | None = None) -> list[Proc]:
    """[root_pid] 與它底下的整棵行程樹，先序（根在最前面）。

    [procs] 給測試塞假資料用；不給就拍一張真的快照並讀建立時間。
    子行程一定比父行程晚建立：pid 被重用時，一個早就存在的行程會看起來像某個新行程的
    子孫（它的父 pid 剛好等於新行程的 pid），用建立時間把它擋掉。
    """
    real = procs is None
    raw = snapshot() if real else procs
    info: dict[int, tuple[int, str]] = {}
    kids: dict[int, list[int]] = {}
    for pid, ppid, name in raw:
        info[pid] = (ppid, name)
        kids.setdefault(ppid, []).append(pid)
    if root_pid not in info:
        return []
    out: list[Proc] = []
    stack: list[tuple[int, int]] = [(root_pid, 0)]     # (pid, 父行程的建立時間)
    seen: set[int] = set()
    while stack:
        pid, parent_created = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        created = _created(pid) if real else 0
        if created and parent_created and created < parent_created:
            continue                                   # pid 重用的假子孫
        ppid, name = info[pid]
        out.append(Proc(pid=pid, ppid=ppid, name=name, created=created))
        for child in kids.get(pid, ()):
            if child != pid:
                stack.append((child, created))
    return out


def _alive(p: Proc) -> bool:
    """還活著，而且還是當初記下的那一個（建立時間相同）。"""
    if not _WIN:
        return False
    h = _k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, p.pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        if not _k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return False
    finally:
        _k32.CloseHandle(h)
    return not p.created or _created(p.pid) == p.created


def _terminate(pid: int) -> bool:
    h = _k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(_k32.TerminateProcess(h, 1))
    finally:
        _k32.CloseHandle(h)


def reap(tree: list[Proc], grace: float = 3.0) -> list[Proc]:
    """CLI 關閉之後呼叫：給 [grace] 秒讓它們自己收，還活著的輔助行程收掉。

    回傳真的收掉了哪些（空清單＝都自己結束了，正常情況）。**阻塞呼叫**，
    從事件迴圈裡用要包 `asyncio.to_thread`。
    """
    if not _WIN:
        return []
    helpers = [p for p in tree if is_helper(p.name)]
    if not helpers:
        return []
    deadline = time.monotonic() + grace
    while any(_alive(p) for p in helpers):
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    else:
        return []
    killed: list[Proc] = []
    # 反過來收（先序的反序≈葉先根後）：父行程先死的話，子孫還在跑，沒差；
    # 但子孫先收掉，父行程就不會在收尾時又去拉起新的
    for p in reversed(helpers):
        if _alive(p) and _terminate(p.pid):
            killed.append(p)
    return killed
