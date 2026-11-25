#!/bin/zsh
# 安装后台同步：launchd 每 5 分钟跑一次 sync_once.py，页面关着也不丢消息。
# 只读 Hermes 输出目录，只写本项目的 hub.db 与 logs/sync.log。
set -u

ROOT="$(cd "$(dirname "${0:A}")" && pwd)"
LABEL="com.fanchuang.hermes-hub-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3 || true)"
UID_NUM="$(id -u)"

log() { echo "[hub-sync] $*"; }
fail() { echo "[hub-sync] 失败: $*" >&2; exit 1; }

[[ -n "$PY" ]] || fail "找不到 python3"
[[ -f "$ROOT/sync_once.py" ]] || fail "缺少 sync_once.py"
rm -f "$ROOT/.no_auto_sync"
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$ROOT/sync_once.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>LowPriorityIO</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$ROOT/logs/sync.log</string>
  <key>StandardErrorPath</key>
  <string>$ROOT/logs/sync.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$UID_NUM" "$PLIST" 2>/dev/null
launchctl bootstrap "gui/$UID_NUM" "$PLIST" || fail "launchctl bootstrap 失败"
launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || fail "服务未注册成功"

"$PY" "$ROOT/sync_once.py" >/dev/null 2>&1 || fail "首次同步失败，看 logs/sync.log"
log "已安装，每 5 分钟同步一次：$PLIST"
log "日志：$ROOT/logs/sync.log"
