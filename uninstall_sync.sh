#!/bin/zsh
# 卸载后台同步，只动本项目自己注册的 launchd 服务。
set -u

LABEL="com.fanchuang.hermes-hub-sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM" "$PLIST" 2>/dev/null
rm -f "$PLIST"
touch "$(cd "$(dirname "$0")" && pwd)/.no_auto_sync"
echo "[hub-sync] 已卸载 $LABEL；just_run.sh 不会再自动安装"
