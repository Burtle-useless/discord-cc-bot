#!/bin/bash
# macOS 一鍵安裝：Python 環境 → 套件 → Discord bot 設定 → 登入 Claude → 乾跑檢查 → 開機自動啟動。
# 可以重複跑，做過的步驟會跳過；只有非人不可的地方（建 Discord bot、貼 token、瀏覽器登入）才會停下來等。
# CI 用：SETUP_CI=1 時不問問題、不碰 Discord、不登入、不裝服務，只驗證環境與乾跑檢查。
set -u
cd "$(dirname "$0")" || exit 1

B=$(printf '\033[1m'); R=$(printf '\033[31m'); G=$(printf '\033[32m'); Y=$(printf '\033[33m'); N=$(printf '\033[0m')
step() { printf '\n%s== %s ==%s\n' "$B" "$1" "$N"; }
ok()   { printf '%s✓ %s%s\n' "$G" "$1" "$N"; }
warn() { printf '%s! %s%s\n' "$Y" "$1" "$N"; }
die()  { printf '%s✗ %s%s\n' "$R" "$1" "$N"; exit 1; }
CI=${SETUP_CI:-}
pause() { [ -n "$CI" ] || read -r -p "$1" _; }

[ "$(uname)" = "Darwin" ] || die "這支腳本只給 macOS 用；Windows 請照 README.md 安裝"

# ── 1. Python 環境 ───────────────────────────────────────────────────────────
# 用 uv 建虛擬環境：它會自己下載合適的 Python，不需要 Homebrew，也不用輸入管理員密碼
step "1/6 Python 環境"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    echo "安裝 uv（管理 Python 的工具）..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv 安裝失敗，確認網路正常後重跑 ./setup.sh"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die "uv 裝好了卻找不到，關掉終端機重開一個再跑 ./setup.sh"
fi
if [ ! -x .venv/bin/python ]; then
    uv venv --python 3.12 .venv || die "建虛擬環境失敗"
fi
PY="$(pwd)/.venv/bin/python"
ok "虛擬環境 $("$PY" --version)"

# ── 2. 套件 ──────────────────────────────────────────────────────────────────
# 只裝核心＋截圖＋排程；語音與語意搜尋要下載好幾 GB，需要時再 pip install -r requirements.txt
step "2/6 安裝套件"
PKGS=$(grep -E '^(discord\.py|claude-agent-sdk|Pillow|croniter)' requirements.txt)
# shellcheck disable=SC2086
uv pip install --python "$PY" $PKGS || die "套件安裝失敗，把上面的錯誤訊息整段留著求助"
ok "套件裝好了"

# 這個 Python 的 TLS 憑證驗證能不能用（bot 連 Discord 靠它）。
# 收到任何 HTTP 回應就代表握手成功——Discord 前面的 Cloudflare 會對 Python 預設的 User-Agent 回 403
if ! ERR=$("$PY" - 2>&1 <<'PYEOF'
import urllib.error, urllib.request
try:
    urllib.request.urlopen("https://discord.com/api/v10/gateway", timeout=15)
except urllib.error.HTTPError:
    pass
PYEOF
); then
    printf '%s\n' "$ERR" | tail -n 3
    die "Python 連不上 https://discord.com（網路不通，或憑證驗證失敗）。確認網路後重跑；仍失敗請回報上面的錯誤"
fi
ok "HTTPS 連線正常"

# ── 3. Discord bot ───────────────────────────────────────────────────────────
[ -f .env ] || cp .env.example .env
envget() { sed -n "s/^$1=//p" .env | tail -n 1; }
envset() {
    "$PY" - "$1" "$2" <<'PYEOF'
import sys, pathlib
k, v = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env")
lines = p.read_text(encoding="utf-8").splitlines()
for i, line in enumerate(lines):
    if line.startswith(k + "="):
        lines[i] = f"{k}={v}"
        break
else:
    lines.append(f"{k}={v}")
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
PYEOF
}
api() {
    curl -fsS -H "Authorization: Bot $TOKEN" -H "User-Agent: DiscordBot (setup.sh, 1)" \
        "https://discord.com/api/v10$1"
}
js() { "$PY" -c "import sys, json; d = json.load(sys.stdin); $1"; }

step "3/6 Discord bot"
if [ -n "$CI" ]; then
    [ -n "$(envget DISCORD_TOKEN)" ] || envset DISCORD_TOKEN ci-dummy-token
    [ -n "$(envget ALLOWED_USER)" ] || envset ALLOWED_USER 1
    warn "CI 模式：略過 Discord 設定"
else
    TOKEN=$(envget DISCORD_TOKEN)
    while :; do
        if [ -n "$TOKEN" ] && ME=$(api /users/@me 2>/dev/null); then
            break
        fi
        [ -n "$TOKEN" ] && warn "這個 token 無效（可能貼錯或已經重設過），重新拿一次"
        cat <<EOF

接下來要在 Discord 開發者網站建一個 bot，網頁會自動打開：
  1. 右上角 New Application，取個名字，同意條款後建立
  2. 左邊點 Bot → Reset Token → 複製那串 token
  3. 同一頁往下找 Message Content Intent，打開後按 Save Changes
EOF
        open "https://discord.com/developers/applications" 2>/dev/null
        read -r -s -p "把 token 貼在這裡按 Enter（畫面不會顯示）：" TOKEN; echo
        TOKEN=$(printf '%s' "$TOKEN" | tr -d '[:space:]')
    done
    envset DISCORD_TOKEN "$TOKEN"
    ok "token 有效，bot 名稱：$(printf '%s' "$ME" | js 'print(d["username"])')"

    # Message Content Intent 沒開，bot 會上線但讀不到訊息內容——最常見的「不回話」原因
    while :; do
        APP=$(api /applications/@me) || die "讀不到 bot 的應用程式資料"
        # 1<<18 GATEWAY_MESSAGE_CONTENT、1<<19 GATEWAY_MESSAGE_CONTENT_LIMITED
        if [ "$(printf '%s' "$APP" | js 'print(int(bool(d.get("flags", 0) & (3 << 18))))')" = 1 ]; then
            ok "Message Content Intent 已開"
            break
        fi
        warn "Message Content Intent 還沒開：開發者網站 → Bot → Message Content Intent 打開並按 Save Changes"
        read -r -p "開好按 Enter 重新檢查（確定開了卻一直過不了，輸入 s 略過）：" a
        [ "$a" = s ] && break
    done

    # 擁有者就是建 bot 的人，直接拿來當 ALLOWED_USER，不必開開發者模式複製 ID
    if [ -z "$(envget ALLOWED_USER)" ]; then
        OWNER=$(printf '%s' "$APP" | js 'o = d.get("owner") or {}; print(o.get("id", "") if not d.get("team") else "")')
        if [ -n "$OWNER" ]; then
            envset ALLOWED_USER "$OWNER"
            ok "只接受 bot 擁有者 $(printf '%s' "$APP" | js 'print(d["owner"]["username"])') 的指令"
        else
            echo "這個 bot 屬於 Discord 團隊，無法自動判斷是誰，要手動填你的使用者 ID。"
            echo "Discord 設定 → 進階 → 開發者模式打開，對自己的名字按右鍵 → 複製使用者 ID"
            while :; do
                read -r -p "貼上你的使用者 ID：" UID_IN
                [[ "$UID_IN" =~ ^[0-9]{15,21}$ ]] && break
                warn "ID 應該是 15 到 21 位數字"
            done
            envset ALLOWED_USER "$UID_IN"
        fi
    fi

    # 邀請進伺服器
    if [ "$(api /users/@me/guilds | js 'print(len(d))')" = 0 ]; then
        APP_ID=$(printf '%s' "$APP" | js 'print(d["id"])')
        # 權限：Manage Channels、Send Messages、Attach Files、Read Message History
        URL="https://discord.com/oauth2/authorize?client_id=$APP_ID&scope=bot%20applications.commands&permissions=100368"
        echo
        echo "把 bot 邀進你自己的私人伺服器（沒有的話先在 Discord 左邊按 + 建一個）。邀請網頁會自動打開："
        echo "  $URL"
        open "$URL" 2>/dev/null
        while [ "$(api /users/@me/guilds | js 'print(len(d))')" = 0 ]; do
            read -r -p "邀請完按 Enter：" _
        done
    fi
    ok "bot 已經在伺服器裡"
fi
[ -n "$(envget CONFIRM_DANGEROUS)" ] || envset CONFIRM_DANGEROUS 1

# ── 4. Claude 登入 ───────────────────────────────────────────────────────────
# SDK 套件裡自帶一份 Claude Code CLI，用它登入就好，不必另外裝 Node.js
step "4/6 Claude 登入"
CLAUDE=$("$PY" -c "import claude_agent_sdk, pathlib; print(pathlib.Path(claude_agent_sdk.__file__).parent / '_bundled' / 'claude')")
[ -x "$CLAUDE" ] || CLAUDE=$(command -v claude || true)
[ -n "$CLAUDE" ] || die "找不到 Claude Code CLI（claude-agent-sdk 沒帶到執行檔）"
logged_in() { "$CLAUDE" auth status 2>/dev/null | "$PY" -c "import sys, json; sys.exit(0 if json.load(sys.stdin).get('loggedIn') else 1)" 2>/dev/null; }
if [ -n "$CI" ]; then
    "$CLAUDE" --version >/dev/null || die "Claude Code CLI 執行失敗"
    warn "CI 模式：略過登入（CLI $("$CLAUDE" --version) 可以執行）"
elif logged_in; then
    ok "已經登入 Claude"
else
    echo "需要付費方案（Pro 以上）。免費版不能用 Claude Code。"
    echo "瀏覽器會打開，用你的 Claude 帳號登入並按授權。"
    pause "準備好按 Enter："
    "$CLAUDE" auth login --claudeai
    logged_in || die "還沒登入成功，重跑 ./setup.sh 會從這一步繼續"
    ok "登入成功"
fi
if [ -z "$(envget BUTLER_PLAN)" ] && [ -z "$CI" ]; then
    PLAN=$("$CLAUDE" auth status 2>/dev/null | "$PY" -c "import sys, json; print(json.load(sys.stdin).get('subscriptionType') or '')" 2>/dev/null)
    case "$PLAN" in pro|max|enterprise) envset BUTLER_PLAN "$PLAN"; ok "方案：$PLAN" ;; esac
fi

# ── 5. 乾跑檢查 ──────────────────────────────────────────────────────────────
step "5/6 乾跑檢查"
OUT=$("$PY" -m lu --check 2>&1)
printf '%s\n' "$OUT" | tail -n 5
printf '%s\n' "$OUT" | tail -n 1 | grep -qx OK || die "乾跑檢查沒過，上面就是錯誤訊息"
ok "程式與設定都接好了"
chmod +x ./*.sh

# ── 6. 開機自動啟動 ──────────────────────────────────────────────────────────
step "6/6 啟動"
if [ -n "$CI" ]; then
    warn "CI 模式：不裝服務"
    exit 0
fi
LOG=discord_bot.log
size() { [ -f "$LOG" ] && stat -f%z "$LOG" || echo 0; }
BEFORE=$(size)
read -r -p "要設成登入後自動啟動、當掉自動重啟嗎？[Y/n] " a
if [ "$a" = n ] || [ "$a" = N ]; then
    ./start.sh
else
    ./install_service.sh || die "服務註冊失敗"
fi
# 等它上線：看這次啟動之後新寫進 log 的「lu 上線」那行（實例鎖的 port 在連 Discord 之前就綁了，不能拿來判斷）
online() {
    [ "$(size)" -lt "$BEFORE" ] && BEFORE=0   # start.sh 會把舊 log 滾掉
    tail -c +"$((BEFORE + 1))" "$LOG" 2>/dev/null | grep -q 'lu 上線'
}
for _ in $(seq 1 45); do
    online && break
    sleep 1
done
if online; then
    ok "bot 上線了"
    cat <<EOF

到 Discord 你的伺服器看：會多一個分類和入口頻道，在入口頻道打一句話就會開新對話。
第一次可以先叫它做件無害的事，例如「列出我家目錄有哪些資料夾」。
macOS 跳出「想要取用文件／桌面」之類的權限視窗時按允許。

之後的操作：./stop.sh 停止、./restart.sh 重啟、tail -f discord_bot.log 看紀錄
EOF
else
    warn "45 秒內沒看到 bot 上線，最後的紀錄："
    tail -n 30 discord_bot.log 2>/dev/null
    exit 1
fi
