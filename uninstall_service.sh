#!/bin/bash
# 移除 launchd 服務：停掉 bot，之後登入也不會再自動啟動。
LABEL=com.ccbot.lu
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null
rm -f "$PLIST"
echo "已移除 $LABEL"
