#!/bin/bash
# ポート 8000 で動いている LifeOS を停止
set -euo pipefail

PIDS=$(lsof -ti:8000 2>/dev/null || true)
if [[ -z "$PIDS" ]]; then
  osascript -e 'display notification "ポート 8000 で動作中のプロセスはありません" with title "LifeOS"'
  exit 0
fi

kill $PIDS 2>/dev/null || true
osascript -e 'display notification "LifeOS を停止しました" with title "LifeOS"'
