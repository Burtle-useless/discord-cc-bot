# cc-bot watchdog：由 Windows 排程每 5 分鐘執行一次。
# 邏輯：開關檔存在（bot 啟動時自建）且實例鎖 port 47361 沒人聽（＝bot 死了）才拉活。
# 用 stop_bot.vbs 正常關閉會先刪開關檔，故意關機不會被詐屍。
$bot = Split-Path -Parent $MyInvocation.MyCommand.Path
$marker = Join-Path $bot "watchdog_enabled"
if (-not (Test-Path $marker)) { exit 0 }
$alive = Get-NetTCPConnection -LocalPort 47361 -State Listen -ErrorAction SilentlyContinue
if ($alive) { exit 0 }
Add-Content -Path (Join-Path $bot "watchdog.log") -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] bot 未在線，watchdog 自動拉活"
# 走 _restart_now.ps1 而不是 restart_bot.vbs：後者用 `>` 重導向，一啟動就把
# discord_bot.log 清空——而 watchdog 拉活的當下，那份 log 正是 bot 為什麼死的
# 唯一證據。_restart_now.ps1 會先把它輪替成 .log.1 再啟動，死因才留得下來。
Start-Process -FilePath "powershell.exe" -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + (Join-Path $bot "_restart_now.ps1") + '"')
) -WindowStyle Hidden
