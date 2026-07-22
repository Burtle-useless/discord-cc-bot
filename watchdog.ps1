# cc-bot watchdog：由 Windows 排程每 5 分鐘執行一次。
# 邏輯：開關檔存在（bot 啟動時自建）且實例鎖 port 47361 沒人聽（＝bot 死了）才拉活。
# 用 stop_bot.vbs 正常關閉會先刪開關檔，故意關機不會被詐屍。
$bot = Split-Path -Parent $MyInvocation.MyCommand.Path
$marker = Join-Path $bot "watchdog_enabled"
if (-not (Test-Path $marker)) { exit 0 }
$alive = Get-NetTCPConnection -LocalPort 47361 -State Listen -ErrorAction SilentlyContinue
if ($alive) { exit 0 }
Add-Content -Path (Join-Path $bot "watchdog.log") -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] bot 未在線，watchdog 自動拉活"
Start-Process -FilePath "C:\Windows\System32\wscript.exe" -ArgumentList ('"' + (Join-Path $bot "restart_bot.vbs") + '"')
