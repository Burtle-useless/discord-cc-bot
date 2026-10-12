#!/bin/bash
# 重啟 bot。注意：不要在 bot 自己的對話裡叫 Claude 跑這支，它跑在 bot 裡面，會把自己關掉。
cd "$(dirname "$0")" || exit 1

LABEL=com.ccbot.lu
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
    echo "已重啟 launchd 服務"
    exit 0
fi

./stop.sh
./start.sh
