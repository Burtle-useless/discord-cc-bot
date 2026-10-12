#!/bin/bash
# 註冊成 launchd 使用者服務：登入後自動啟動、當掉自動重啟（取代 Windows 版的 watchdog）。
# 正常結束（exit 0）不會被拉回來，只有異常結束才會。
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
PY="$DIR/.venv/bin/python"
LABEL=com.ccbot.lu
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$PY" ]; then
    echo "找不到 .venv/bin/python，請先跑 ./setup.sh"
    exit 1
fi

# 手動啟動的那一隻先停掉，避免兩隻搶同一個 port
PID=$(lsof -nP -tiTCP:47361 -sTCP:LISTEN 2>/dev/null)
[ -n "$PID" ] && kill "$PID" && sleep 2

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>-m</string>
        <string>lu</string>
    </array>
    <key>WorkingDirectory</key><string>$DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>LANG</key><string>en_US.UTF-8</string>
        <key>PYTHONUNBUFFERED</key><string>1</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key><false/>
    </dict>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$DIR/discord_bot.log</string>
    <key>StandardErrorPath</key><string>$DIR/discord_bot.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "已註冊並啟動 ${LABEL}，看 log：tail -f discord_bot.log"
