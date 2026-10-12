#!/bin/bash
# 在背景啟動 bot（不佔終端機），輸出寫到 discord_bot.log。
# 上一份 log 先滾成 discord_bot.log.1，當掉時的錯誤訊息才不會被新的一次蓋掉。
# 有裝 launchd 服務（install_service.sh）的話改用服務啟動，不要兩隻一起跑。
cd "$(dirname "$0")" || exit 1

LABEL=com.ccbot.lu
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl kickstart "gui/$(id -u)/$LABEL"
    echo "已透過 launchd 服務啟動（${LABEL}）"
    exit 0
fi

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "找不到 .venv/bin/python，請先跑 ./setup.sh"
    exit 1
fi

if lsof -nP -iTCP:47361 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "bot 已經在跑了"
    exit 0
fi

[ -f discord_bot.log ] && mv -f discord_bot.log discord_bot.log.1
nohup "$PY" -m lu > discord_bot.log 2>&1 &
echo "已在背景啟動（PID $!），看 log：tail -f discord_bot.log"
