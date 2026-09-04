"""白名單：誰可以用（allowed_users）、哪些頻道可以用（allowed_channels）。

兩份都存在 `data\\` 底下，**每次改動都存檔**。舊 cc-bot 的 allowed_channels 七處只改
記憶體、兩處存檔，長期跟實際狀態對不上（健檢 B21）；這裡收成一個類別，
改了就寫。讀失敗回舊值不回空：一次 PermissionError 就把清單清空、下次存檔
把整份抹掉，是舊版 B3 那類病。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .i18n import t

log = logging.getLogger(__name__)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _load_ids(path: Path) -> set[int] | None:
    """讀一份 id 清單。檔案不存在＝空；讀不出來回 None（呼叫端保留原值）。"""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(x) for x in data}
    except Exception:  # noqa: BLE001
        log.exception("讀 %s 失敗，沿用記憶體裡的清單", path.name)
        return None


class Auth:
    """使用者與頻道白名單。主帳號永遠在使用者清單裡，拿不掉。"""

    def __init__(self, data_dir: Path, owner_id: int) -> None:
        self.owner_id = owner_id
        self._users_file = data_dir / "allowed_users.json"
        self._channels_file = data_dir / "allowed_channels.json"
        self.users: set[int] = {owner_id}
        self.channels: set[int] = set()
        # 入口頻道不在白名單裡，但那裡可以用指令（/sessions 之類）；由 sidebar 設定
        self.entry_channel_id: int | None = None
        self.load()

    def load(self) -> None:
        users = _load_ids(self._users_file)
        if users is not None:
            self.users = users | {self.owner_id}
        channels = _load_ids(self._channels_file)
        if channels is not None:
            self.channels = channels

    def save_users(self) -> None:
        try:
            _atomic_write(self._users_file, json.dumps(sorted(self.users)))
        except Exception:  # noqa: BLE001
            log.exception("寫 allowed_users.json 失敗")

    def save_channels(self) -> None:
        try:
            _atomic_write(self._channels_file, json.dumps(sorted(self.channels)))
        except Exception:  # noqa: BLE001
            log.exception("寫 allowed_channels.json 失敗")

    # ── 使用者 ──
    def add_user(self, uid: int) -> None:
        self.users.add(uid)
        self.save_users()

    def remove_user(self, uid: int) -> None:
        if uid == self.owner_id:
            return
        self.users.discard(uid)
        self.save_users()

    def user_ok(self, uid: int) -> bool:
        return uid in self.users

    # ── 頻道 ──
    def add_channel(self, cid: int, persist: bool = True) -> None:
        self.channels.add(cid)
        if persist:
            self.save_channels()

    def remove_channel(self, cid: int) -> None:
        self.channels.discard(cid)
        self.save_channels()

    def channel_ok(self, cid: int) -> bool:
        return cid in self.channels

    # ── 指令閘門 ──
    async def check(self, interaction: Any, owner_only: bool = False) -> bool:
        """slash 指令的授權檢查；不過就回一則 ephemeral 說明並回 False。

        入口頻道雖不處理一般訊息，但允許在那邊用指令（/sessions 等）。
        interaction 只用到 channel_id／user.id／response.send_message，測試可以塞假的。
        """
        cid = interaction.channel_id
        if cid not in self.channels and cid != self.entry_channel_id:
            await interaction.response.send_message(t("no_permission"), ephemeral=True)
            return False
        if owner_only and interaction.user.id != self.owner_id:
            await interaction.response.send_message(t("owner_only"), ephemeral=True)
            return False
        if interaction.user.id not in self.users:
            await interaction.response.send_message(t("no_permission"), ephemeral=True)
            return False
        return True
