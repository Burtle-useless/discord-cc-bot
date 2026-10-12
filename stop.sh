#!/bin/bash
# 停止 bot。launchd 服務模式要用 bootout 停，直接 kill 會被 launchd 當成當掉而拉回來。
cd "$(dirname "$0")" || exit 1

LABEL=com.ccbot.lu
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)" "$PLIST"
    echo "已停止 launchd 服務（下次登入仍會自動啟動，要永久關掉請跑 uninstall_service.sh）"
    exit 0
fi

PID=$(lsof -nP -tiTCP:47361 -sTCP:LISTEN 2>/dev/null)
if [ -z "$PID" ]; then
    echo "bot 沒有在跑"
    exit 0
fi
kill "$PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$PID" 2>/dev/null || { echo "已停止（PID ${PID}）"; exit 0; }
    sleep 1
done
kill -9 "$PID" 2>/dev/null
echo "十秒內沒自己結束，已強制停止（PID ${PID}）"
