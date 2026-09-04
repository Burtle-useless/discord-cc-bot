"""接線乾跑：不連 Discord、不碰正式資料，確認陸真的掛在引擎上。

這支測試存在的理由是「靜默錯誤」：資料目錄指錯只會讓兩個前端共用一份 session、
profile 沒註冊只會套到引擎的預設 prompt、append 帶換行只會讓 CLI 卡 60 秒——
三種都不會拋例外，只會在上線之後才發現。

引擎用 repo 自帶的 `server/`（見 `lu/tests/_env.py`）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env  # noqa: E402

_env.setup("lu-boot-")

from lu import settings as settings_mod  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


def _settings(tmp: Path) -> object:
    """一份假設定：資料目錄在暫存區、沒有 token（乾跑用不到）。"""
    return settings_mod.load(
        env_file=tmp / "no-such.env",
        environ={
            "ALLOWED_USER": "12345",
            "BUTLER_SERVER_DIR": str(_env.butler_server()),
            "LU_DATA_DIR": str(tmp / "data"),
            "BOT_LANG": "zh-TW",
            "BUTLER_CWD": str(tmp),
            # 引擎才讀得懂的鍵，陸只負責原樣轉交（見 settings.ENGINE_ENV_KEYS）
            "DEFAULT_MODEL": "claude-haiku-4-5",
        },
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        st = _settings(tmp)

        print("\n[設定]")
        check("資料目錄指到暫存區", st.data_dir == tmp / "data", str(st.data_dir))
        check("沒有 token 也載得起來", st.token is None)
        check("缺 ALLOWED_USER 會炸", _raises(lambda: settings_mod.load(
            env_file=tmp / "no-such.env", environ={})))
        # 引擎跟這個 repo 一起 clone 下來，所以不設 BUTLER_SERVER_DIR 是正常情況
        check("沒設 BUTLER_SERVER_DIR 就用自帶的 server/", settings_mod.load(
            env_file=tmp / "no-such.env", environ={"ALLOWED_USER": "12345"},
        ).butler_server == settings_mod.DEFAULT_SERVER_DIR)
        check("BUTLER_SERVER_DIR 指到沒有 engine 的目錄也會炸", _raises(
            lambda: settings_mod.load(env_file=tmp / "no-such.env", environ={
                "ALLOWED_USER": "12345", "BUTLER_SERVER_DIR": str(tmp)})))

        print("\n[接線]")
        from lu.bootstrap import build, prepare
        prepare(st)
        import config  # noqa: PLC0415 — 一定要在 prepare 之後
        check("引擎的資料目錄是陸的", Path(config.DATA_DIR) == st.data_dir,
              f"{config.DATA_DIR}")
        # .env 寫給引擎的鍵沒轉過去的話，這裡會是引擎的預設值而且完全不報錯
        check("引擎讀得到 .env 的 DEFAULT_MODEL",
              config.DEFAULT_MODEL == "claude-haiku-4-5", config.DEFAULT_MODEL)
        # install_ports=False：engine 的掛勾是模組全域，測試裝上去會影響同進程的其他測試
        ctx = build(st, install_ports=False)
        check("Worker 建得起來", ctx.worker is not None)
        check("主帳號在白名單裡", ctx.auth.user_ok(12345) and not ctx.auth.user_ok(999))

        print("\n[profile]")
        from engine import profiles
        p = profiles.resolve("dc:123")
        check("dc: 前綴解析成陸", p.name == "lu", p.name)
        check("append 一個換行都沒有", "\n" not in p.append,
              f"{p.append.count(chr(10))} 個換行")
        check("append 不是空的", len(p.append) > 200, f"{len(p.append)} 字")
        check("不影響引擎自己那條主對話", profiles.resolve(config.PRIMARY_CONV).name != "lu")

        print("\n[選項組得出來]")
        from engine.options import build_options
        from engine.state import get_state
        fe = ctx.frontend_for("dc:123")
        opts = build_options(get_state("dc:123"), fe)
        check("有模型", bool(opts.model), str(opts.model))
        check("掛了傳檔工具", "files" in (opts.mcp_servers or {}), str(list(opts.mcp_servers)))
        check("沒掛 App 端的生活工具", "agenda" not in (opts.mcp_servers or {}),
              str(list(opts.mcp_servers)))
        check("system prompt 是疊加不是取代",
              isinstance(opts.system_prompt, dict) and opts.system_prompt.get("type") == "preset",
              str(type(opts.system_prompt)))

        print("\n[前端與對話 id]")
        from lu.profile import channel_id, conv_id
        check("頻道 id ⇄ 對話 id", conv_id(555) == "dc:555" and channel_id("dc:555") == 555)
        check("不是陸的對話回 None", channel_id("main") is None)
        check("同一條對話拿到同一個前端", ctx.frontend_for("dc:123") is fe)

    print(f"\n{'全部通過' if not FAILED else '失敗：' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
