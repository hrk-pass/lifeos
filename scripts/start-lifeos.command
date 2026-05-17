#!/bin/bash
# ダブルクリック（Dock に置いても可）で LifeOS を起動
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -d .venv ]]; then
  osascript -e 'display alert "LifeOS" message ".venv がありません。README のセットアップを実行してください。"'
  exit 1
fi

# 既に起動中ならブラウザだけ開く
if lsof -ti:8000 >/dev/null 2>&1; then
  open "http://127.0.0.1:8000/"
  osascript -e 'display notification "すでに起動中です（ポート 8000）" with title "LifeOS"'
  exit 0
fi

source .venv/bin/activate

echo "LifeOS を起動しています…"
echo "  一覧: http://127.0.0.1:8000/"
echo "  iPhone POST: http://$(ipconfig getifaddr en0 2>/dev/null || echo 'YOUR_MAC_IP'):8000/capture"
echo "  終了: Ctrl+C"
echo ""

open "http://127.0.0.1:8000/"

# iPhone ショートカット用は 0.0.0.0（同一 Wi‑Fi）
exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
