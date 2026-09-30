#!/bin/bash
# 开发模式启动桌面端。
#   ./run_desktop.sh            → 起 Electron 壳（主进程自动拉起 sidecar）
#   ./run_desktop.sh --browser  → 不起 Electron，只开 sidecar 并用浏览器调试同一套 UI
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
PY="./.venv/bin/python"
[ -x "$PY" ] || PY=python3

if [ "$1" = "--browser" ]; then
  echo "[1/2] 启动本地服务（自动选空闲端口）…"
  LOG=$(mktemp)
  "$PY" -m tingchao.local_api --port 0 > "$LOG" 2>&1 &
  SRV=$!
  trap 'kill $SRV 2>/dev/null; rm -f "$LOG"' EXIT
  for _ in $(seq 1 30); do
    PORT=$(grep -o 'SIDECAR_PORT=[0-9]*' "$LOG" | head -1 | cut -d= -f2)
    [ -n "$PORT" ] && break
    sleep 0.5
  done
  if [ -z "$PORT" ]; then echo "[!] 启动失败："; cat "$LOG"; exit 1; fi
  echo "[2/2] 打开浏览器 http://127.0.0.1:$PORT"
  open "http://127.0.0.1:$PORT" 2>/dev/null || xdg-open "http://127.0.0.1:$PORT"
  wait $SRV
  exit 0
fi

if [ ! -x "electron/node_modules/.bin/electron" ]; then
  echo "[!] 未安装 Electron 依赖，先执行："
  echo "    cd electron && ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/ \\"
  echo "      npm install --registry=https://registry.npmmirror.com"
  exit 1
fi

echo "启动 听潮 桌面端（Electron + sidecar）…"
cd electron && exec ./node_modules/.bin/electron .
