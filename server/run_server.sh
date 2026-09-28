#!/bin/bash
# 启动授权服务（首次运行自动建 venv、装依赖）
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PY=python3
VENV="$DIR/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "[1/3] 首次启动，创建虚拟环境..."
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
  "$VENV/bin/python" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
fi

echo "[2/3] 启动授权服务（FastAPI :8000）..."
echo "    首次启动会自动创建管理员账号，密码会写到 admin_credentials.txt"
echo "    Ctrl+C 退出"
echo "---"
exec "$VENV/bin/python" server.py "$@"
