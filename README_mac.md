# Discord CC Bot — macOS

功能、指令列表、安全模型請看 [README.md](README.md)，這份只寫 Mac 上的安裝與啟動。

## 安全提醒（先讀）

這個 bot 用 `bypassPermissions` 執行 Claude Code，**Discord 頻道裡的訊息等於直接在你的 Mac 上執行指令**。

- 放在只有你一個人的私人伺服器，Discord 帳號開兩步驟驗證。
- `ALLOWED_USER` 只能是你自己（`setup.sh` 會自動填成建 bot 的那個帳號）。
- `setup.sh` 預設打開 `CONFIRM_DANGEROUS=1`：刪檔、git push 這類動作會先跳確認按鈕。

## 安裝：一行指令

需要 Claude **Pro 以上**的付費方案（免費版不能用 Claude Code）。打開「終端機」：

```bash
git clone https://github.com/Burtle-useless/discord-cc-bot.git ~/discord-cc-bot
cd ~/discord-cc-bot
./setup.sh
```

（沒有 git 的話，跑 `git` 時 macOS 會問要不要安裝開發者工具，按安裝就好；或者在 GitHub 頁面按 Code → Download ZIP，解壓縮後 `cd` 進去再跑 `./setup.sh`。）

腳本會自己做完這些事：

1. 安裝 [uv](https://docs.astral.sh/uv/)，用它下載 Python 並建立虛擬環境。不需要 Homebrew，也不用輸入管理員密碼。
2. 安裝核心套件。
3. 打開 Discord 開發者網站，等你貼上 bot token，接著自動檢查：token 是否有效、Message Content Intent 有沒有開、bot 進伺服器了沒。邀請連結也會自動產生，`ALLOWED_USER` 會直接用建 bot 的帳號。
4. 用 SDK 自帶的 Claude Code 開瀏覽器登入，不需要另外安裝 Node.js。
5. 做一次乾跑檢查。
6. 設成登入後自動啟動、當掉自動重啟（launchd），等 bot 上線後才結束。

只有下面這幾件事要你自己動手：在 Discord 開發者網站建 bot、複製 token、按邀請、在瀏覽器登入 Claude。中途中斷了就再跑一次 `./setup.sh`，做過的步驟會自動跳過。

語音轉文字和語意搜尋要下載好幾 GB，預設不裝。需要的話之後再跑：

```bash
~/.local/bin/uv pip install --python .venv/bin/python -r requirements.txt
```

## 日常操作

```bash
./start.sh                # 啟動
./stop.sh                 # 停止
./restart.sh              # 重啟
tail -f discord_bot.log   # 看紀錄
./uninstall_service.sh    # 取消開機自動啟動
```

有裝 launchd 服務的話，這幾支會自動改用 launchd 操作。

## 跟 Windows 版的差異

- 啟動、停止、自動重啟改用上面那幾支 `.sh` 和 launchd，不用 `.vbs`、`.ps1` 和 watchdog 排程。
- 給 Claude 的 system prompt 會自動換成 macOS 版（用 Bash，不用 PowerShell）。
- `SHARE_SCRIPT` 可以指向 `.sh`（直接執行）或 `.ps1`（交給 `pwsh`）。
- 停止對話時清理殘留子行程的功能只支援 Windows。Mac 上偶爾會留下閒置的 node 或 MCP 行程，重啟 bot 就會清掉。
- 危險指令確認目前還是以 Windows 與通用指令為主，`killall`、`reboot`、`diskutil erase` 這幾個 Mac 專屬指令還不會跳確認。

## Mac 專屬注意事項

- **權限視窗**：第一次讓它碰文件、桌面、下載資料夾時 macOS 會跳出詢問。服務模式下沒有人按得到，可以到「系統設定 → 隱私權與安全性 → 完整磁碟取用權限」把 `.venv/bin/python` 實際指向的那個 python 加進去，或者把 `.env` 的 `DEFAULT_DIR` 設在這些資料夾以外。
- **截圖（/screenshot）**：要在「螢幕錄製」權限裡允許 python。
- **電腦睡著，bot 就會斷線**：到「系統設定 → 電池（或能源）」把自動睡眠關掉。
- **額度**：Pro 每 5 小時有用量上限，用完 bot 會等額度恢復後自己接著做。用 `/usage` 看還剩多少，`/model` 換成 Sonnet 或 Haiku 比較省。

## 疑難排解

- `setup.sh` 中途失敗：紅字那行就是原因。修好後重跑，會從失敗的那一步繼續。
- bot 上線了卻不回話：確認是在它自己建的入口頻道打字。如果你按了略過 Message Content Intent 檢查，回開發者網站確認它有打開。
- 每則訊息都出錯：Claude 登入過期了，重跑 `./setup.sh` 會重新登入。
- 其他狀況：看 `tail -n 50 discord_bot.log`。
