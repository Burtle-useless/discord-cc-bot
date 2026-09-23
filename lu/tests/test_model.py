"""/model 面板：這一版 Claude Code 跑不動的模型照樣列出來，但選了不套用。

不連 Discord、不問官方：模型清單直接換成固定的幾顆，互動用假的。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 路徑要在 import lu 之前排好：model 面板會 import 引擎。資料目錄也一樣——面板套用時
# 會存檔，engine 的 config 在 import 當下就把資料目錄凍成常數，晚一步設就會寫進
# 正式的 data\。兩件事都在 _env.setup() 裡。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _env  # noqa: E402

_env.setup("lu-model-")

import config  # noqa: E402
from engine import models  # noqa: E402
from engine import state as state_mod  # noqa: E402
from engine.models import ModelInfo  # noqa: E402
from engine.state import get_state  # noqa: E402

from lu import i18n  # noqa: E402
from lu.commands.model import ModelPanel  # noqa: E402

FAILED: list[str] = []
CONV = "dc:4242"

# 假清單：兩顆跑不動（一顆知道要哪一版、一顆不知道），一顆正常
CATALOG = [
    ModelInfo("claude-opus-9[1m]", "claude-opus-9", "Opus 9", "主力", family="opus",
              available=False, requires_cli="9.9.9"),
    ModelInfo("claude-sonnet-9", "claude-sonnet-9", "Sonnet 9", "例行工作", family="sonnet",
              available=False),
    ModelInfo("claude-opus-8[1m]", "claude-opus-8", "Opus 8", "上一代主力", family="opus",
              tier="more"),
]


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + extra if extra else ''}")
    if not cond:
        FAILED.append(name)


class _Worker:
    def is_running(self, _conv: str) -> bool:
        return False


class _Ctx:
    worker = _Worker()


class _Resp:
    def __init__(self) -> None:
        self.content: str | None = None

    async def edit_message(self, *, content: str, view: object) -> None:
        self.content = content


class _Inter:
    def __init__(self, value: str) -> None:
        self.data = {"values": [value]}
        self.response = _Resp()


def _descs(panel: ModelPanel) -> dict[str, str | None]:
    """模型那個下拉選單：值 → 說明。"""
    sel = panel.children[1]
    return {o.value: o.description for o in sel.options}


async def test_listing() -> None:
    print("\n[跑不動的照樣列，說明改成要更新]")
    i18n.set_lang("zh-TW")
    panel = ModelPanel(_Ctx(), CONV, 1)
    d = _descs(panel)
    check("三顆都列出來", all(m.value in d for m in CATALOG), str(list(d)))
    check("知道版本就寫出版本", d.get("claude-opus-9[1m]") == "要 Claude Code 9.9.9 以上才能用",
          str(d.get("claude-opus-9[1m]")))
    check("不知道版本就只說要更新", d.get("claude-sonnet-9") == "要更新 Claude Code 才能用",
          str(d.get("claude-sonnet-9")))
    check("跑得動的照原本的說明", d.get("claude-opus-8[1m]") == "上一代主力",
          str(d.get("claude-opus-8[1m]")))

    i18n.set_lang("en")
    d = _descs(ModelPanel(_Ctx(), CONV, 1))
    check("英文也有", d.get("claude-opus-9[1m]") == "Needs Claude Code 9.9.9 or newer",
          str(d.get("claude-opus-9[1m]")))
    i18n.set_lang("zh-TW")


async def test_pick() -> None:
    print("\n[選了跑不動的：不套用、給提示]")
    panel = ModelPanel(_Ctx(), CONV, 1)
    inter = _Inter("claude-opus-9[1m]")
    await panel._on_model(inter)  # noqa: SLF001
    check("這個對話的模型沒被改", get_state(CONV).model is None, str(get_state(CONV).model))
    out = inter.response.content or ""
    check("面板上說沒有套用", "這次沒有套用" in out, panel.notice)
    check("提示帶版本", "9.9.9" in out, panel.notice)
    check("提示說怎麼更新", i18n.t("model_update_how") in out, panel.notice)

    inter = _Inter("claude-opus-8[1m]")
    await panel._on_model(inter)  # noqa: SLF001
    check("跑得動的照樣套用", get_state(CONV).model == "claude-opus-8[1m]",
          str(get_state(CONV).model))
    check("上一次的提示清掉了", "這次沒有套用" not in (inter.response.content or ""),
          panel.notice)

    print("\n[帳號預設那一層也一樣]")
    before = state_mod.default_model
    panel.scope = "account"
    inter = _Inter("claude-sonnet-9")
    await panel._on_model(inter)  # noqa: SLF001
    check("帳號預設沒被改", state_mod.default_model == before, str(state_mod.default_model))
    check("一樣給提示", "這次沒有套用" in (inter.response.content or ""), panel.notice)


async def test_account_default() -> None:
    print("\n[帳號層可以回到官方建議]")
    state_mod.save_defaults(None, state_mod.default_effort)
    panel = ModelPanel(_Ctx(), CONV, 1)
    panel.scope = "account"
    panel._rebuild()  # noqa: SLF001

    def first() -> object:
        return panel.children[1].options[0]

    check("第一列是官方建議", first().label == i18n.t("model_official_default"), first().label)
    check("沒設時勾在官方建議", first().default)
    # 思考強度要照生效的那顆（內建預設）列，跟 App 一樣；拿 None 去查會回完整清單，
    # 預設那顆若不支援思考強度就會列出選不了的等級
    asked: list[object] = []
    real_efforts = models.efforts_for
    models.efforts_for = lambda v: (asked.append(v), real_efforts(v))[1]  # type: ignore[assignment]
    try:
        panel._rebuild()  # noqa: SLF001
    finally:
        models.efforts_for = real_efforts  # type: ignore[assignment]
    check("思考強度照內建預設那顆列", asked == [config.DEFAULT_MODEL], str(asked))
    await panel._on_model(_Inter("claude-opus-8[1m]"))  # noqa: SLF001
    check("選了一顆就存成帳號預設", state_mod.default_model == "claude-opus-8[1m]",
          str(state_mod.default_model))
    check("官方建議不再打勾", not first().default)
    await panel._on_model(_Inter(first().value))  # noqa: SLF001
    check("選回官方建議就清掉帳號預設", state_mod.default_model is None, str(state_mod.default_model))
    check("又勾回官方建議", first().default)


async def test_alias_checked() -> None:
    print("\n[存的是別名：勾在清單那列，不多出原樣那列]")
    st = get_state(CONV)
    st.model = "opus[1m]"
    state_mod.persist(st)
    opts = ModelPanel(_Ctx(), CONV, 1).children[1].options
    checked = [o.value for o in opts if o.default]
    check("勾在 Opus 主力那列", checked == ["claude-opus-9[1m]"], str(checked))
    check("沒有多出原樣的 opus[1m]", all(o.value != "opus[1m]" for o in opts),
          str([o.value for o in opts]))

    st.model = "claude-retired-1"
    state_mod.persist(st)
    opts = ModelPanel(_Ctx(), CONV, 1).children[1].options
    checked = [o.value for o in opts if o.default]
    check("清單上沒有的照樣列出來並打勾", checked == ["claude-retired-1"], str(checked))
    st.model = None
    state_mod.persist(st)


def test_error_text() -> None:
    print("\n[回合撞到同樣的錯]")
    for lang in ("zh-TW", "en"):
        i18n.set_lang(lang)
        text = i18n.err_text("MODEL_NEEDS_UPDATE")
        check(f"有自己的文案（{lang}）", text != i18n.err_text("UNKNOWN"), text)
        check(f"文案說怎麼更新（{lang}）", i18n.t("model_update_how") in text, text)
    i18n.set_lang("zh-TW")


async def main() -> int:
    models.reset_for_tests()
    models.catalog = lambda: list(CATALOG)  # type: ignore[assignment]
    await test_listing()
    await test_pick()
    await test_account_default()
    await test_alias_checked()
    test_error_text()
    print(f"\n{'全部通過' if not FAILED else '失敗：' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
