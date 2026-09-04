"""把舊 cc-bot 的狀態搬成陸（engine）的格式。一次性，但可以重複跑。

**只讀舊檔、只寫新目錄。** 舊檔一個都不動——切換失敗時舊 bot 要能原地起來。

三件要轉格式的：
  discord_session.json  頻道 id → session（值可能是**純字串**或 dict，兩種都要吃）
                        → data/session.json 的 `dc:<id>` → {session_id, forked_from, model, effort, cwd}
  session_titles.json   session_id → 標題（舊版的鍵是 session）
                        → data/titles.json 的 conv_id → 標題（透過上面那份對應換鍵）
  account_defaults.json 欄位名對齊 engine.state（model／effort）

其餘（allowed_users／allowed_channels／schedules／drive_mode／last_version／向量快取／
原話帳本）原樣複製過去。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

# 原樣複製的檔案（存在才複製；已存在於新目錄就不覆蓋，避免把跑過的資料蓋回舊值）
COPY_AS_IS = (
    "allowed_users.json",
    "allowed_channels.json",
    "schedules.json",
    "drive_mode.json",
    "last_version.json",
    "session_vectors_e5.json",
    "user_messages.jsonl",
    "account_plan.json",
)


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 舊檔壞掉就當沒有，不要讓遷移整個停下來
        return None


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def convert_sessions(old: dict[str, Any], default_cwd: str) -> tuple[dict, dict[str, str]]:
    """舊的頻道→session 對應 → engine 的 session map，外加「session_id → conv_id」反查表。

    舊格式兩種並存（早期只存字串，後來換成 dict）：
        "123": "uuid"
        "123": {"session_id": "uuid", "model": null, "effort": null, "cwd": "...", "wt": null}
    `wt`（worktree）刻意丟掉：那是舊 bot 自己的欄位，engine 沒有這個概念。
    """
    out: dict[str, Any] = {}
    by_session: dict[str, str] = {}
    for key, val in (old or {}).items():
        if not str(key).isdigit():
            continue
        conv = f"dc:{key}"
        if isinstance(val, str):
            rec = {"session_id": val or None, "model": None, "effort": None, "cwd": default_cwd}
        elif isinstance(val, dict):
            cwd = str(val.get("cwd") or default_cwd)
            rec = {
                "session_id": val.get("session_id") or None,
                "model": val.get("model") or None,
                "effort": val.get("effort") or None,
                "cwd": cwd,
            }
        else:
            continue
        rec["forked_from"] = None
        out[conv] = rec
        if rec["session_id"]:
            by_session[str(rec["session_id"])] = conv
    return out, by_session


def convert_titles(old: dict[str, Any], by_session: dict[str, str]) -> dict[str, str]:
    """舊的 session_id → 標題，換鍵成 conv_id → 標題。對不上的 session 直接丟掉
    （那是已經沒有頻道的舊對話，標題留著也沒有畫面會用到）。"""
    out: dict[str, str] = {}
    for sid, title in (old or {}).items():
        conv = by_session.get(str(sid))
        if conv and isinstance(title, str) and title.strip():
            out[conv] = title.strip()[:40]
    return out


def convert_defaults(old: Any) -> dict[str, Any] | None:
    """舊的帳號預設 → engine.state 的格式（只有 model／effort 兩個鍵）。"""
    if not isinstance(old, dict):
        return None
    model = old.get("model") or old.get("default_model")
    effort = old.get("effort") or old.get("default_effort")
    if not model and not effort:
        return None
    return {"model": model or None, "effort": effort or None}


def run(old_dir: Path, new_dir: Path, default_cwd: str = "", *, verbose: bool = True) -> dict[str, int]:
    """跑遷移，回各項筆數。重複執行是安全的（同樣的輸入得到同樣的輸出）。"""
    default_cwd = default_cwd or str(Path.home())
    new_dir.mkdir(parents=True, exist_ok=True)
    stats = {"sessions": 0, "titles": 0, "defaults": 0, "copied": 0}

    sessions, by_session = convert_sessions(
        _read_json(old_dir / "discord_session.json") or {}, default_cwd,
    )
    if sessions:
        _write_json(new_dir / "session.json", sessions)
        stats["sessions"] = len(sessions)

    titles = convert_titles(_read_json(old_dir / "session_titles.json") or {}, by_session)
    if titles:
        _write_json(new_dir / "titles.json", titles)
        stats["titles"] = len(titles)

    defaults = convert_defaults(_read_json(old_dir / "account_defaults.json"))
    if defaults:
        _write_json(new_dir / "account_defaults.json", defaults)
        stats["defaults"] = 1

    for name in COPY_AS_IS:
        src = old_dir / name
        dst = new_dir / name
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
            stats["copied"] += 1

    if verbose:
        print(f"session {stats['sessions']} 條、標題 {stats['titles']} 筆、"
              f"帳號預設 {stats['defaults']}、原樣複製 {stats['copied']} 個檔 → {new_dir}")
    return stats


def main() -> int:
    from .settings import BOT_DIR, load
    st = load()
    run(BOT_DIR, st.data_dir, str(st.default_cwd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
