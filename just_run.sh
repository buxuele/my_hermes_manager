#!/bin/zsh
# 总台一键启动：环境检测、复用已运行实例、拉起服务、探测就绪、打开页面。
# 项目自己的启动入口，工作区根目录的同名脚本只做转发。
# 退出时只关闭本次启动的进程，不改别人的状态。
set -u

HERE="$(cd "$(dirname "${0:A}")" && pwd)"
PROJECT_ROOT="$HERE"
cd "$PROJECT_ROOT"

STATE="${HUB_STATE:-$PROJECT_ROOT/.hub_state.json}"
SERVER_LOG="$PROJECT_ROOT/logs/server.log"
SERVER_PID=""
RUN_PY="$(command -v python3 || true)"

log() { echo "[hub] $*"; }
fail() { echo "[hub] 失败: $*" >&2; exit 1; }

[[ -n "$RUN_PY" ]] || fail "找不到 python3"
[[ -f "$PROJECT_ROOT/server.py" ]] || fail "找不到 app_hub/server.py"

read_state() {
  "$RUN_PY" - "$STATE" <<'EOF'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    print(f"{d.get('pid') or 0} {d.get('port') or 0}")
except Exception:
    print("0 0")
EOF
}

STATE_PID=0
STATE_PORT=0
if [[ -f "$STATE" ]]; then
  read -r STATE_PID STATE_PORT <<< "$(read_state)"
fi

if [[ "$STATE_PID" != "0" ]] && kill -0 "$STATE_PID" 2>/dev/null && ps -p "$STATE_PID" -o command= 2>/dev/null | grep -q "server.py"; then
  for _ in {1..20}; do
    CODE="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$STATE_PORT/health" 2>/dev/null)"
    if [[ "$CODE" == "200" ]]; then
      log "总台已在运行：http://127.0.0.1:$STATE_PORT/（进程 $STATE_PID）"
      open "http://127.0.0.1:$STATE_PORT/"
      exit 0
    fi
    sleep 0.2
  done
  log "已有进程 $STATE_PID 但健康检查未通过，看 logs/server.log"
  exit 1
fi

rm -f "$STATE"

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    log "已按进程号 $SERVER_PID 关闭总台"
  fi
  if [[ -f "$STATE" ]]; then
    read -r CUR_PID IGN <<< "$(read_state)"
    if [[ "${CUR_PID:-0}" == "${SERVER_PID:-0}" ]]; then
      rm -f "$STATE"
    fi
  fi
}
trap cleanup EXIT INT TERM

mkdir -p "$PROJECT_ROOT/logs"

SYNC_PLIST="$HOME/Library/LaunchAgents/com.fanchuang.hermes-hub-sync.plist"
if [[ ! -f "$SYNC_PLIST" && ! -f "$PROJECT_ROOT/.no_auto_sync" ]]; then
  log "安装后台同步：launchd 每 5 分钟落库，页面关着也不丢消息"
  "$PROJECT_ROOT/install_sync.sh" || log "后台同步未装上，页面内同步仍可用"
fi

"$RUN_PY" "$PROJECT_ROOT/server.py" > >(tee -a "$SERVER_LOG") 2>&1 &
SERVER_PID=$!

PORT=""
for _ in {1..40}; do
  if [[ -f "$STATE" ]]; then
    read -r IGN PORT <<< "$(read_state)"
    [[ "$PORT" != "0" ]] && break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || { echo; cat "$SERVER_LOG"; fail "服务提前退出，看 logs/server.log"; }
  sleep 0.25
done
[[ -n "$PORT" && "$PORT" != "0" ]] || fail "服务未写出端口，看 logs/server.log"

READY=0
for _ in {1..60}; do
  CODE="$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/health" 2>/dev/null)"
  if [[ "$CODE" == "200" ]]; then READY=1; break; fi
  kill -0 "$SERVER_PID" 2>/dev/null || { echo; cat "$SERVER_LOG"; fail "服务中途退出"; }
  sleep 0.25
done
[[ "$READY" == "1" ]] || fail "端口 $PORT 健康检查未通过，看 logs/server.log"

URL="http://127.0.0.1:$PORT/"
log "总台已开：$URL（进程 $SERVER_PID，按 Ctrl+C 收台）"
open "$URL"
wait "$SERVER_PID"
