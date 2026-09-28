#!/bin/bash
# 启动管理后台（首次运行自动建 venv、装依赖）
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

echo "[2/3] 启动管理后台（Streamlit :8501）..."
echo "    浏览器会自动打开 http://localhost:8501"
echo "    默认管理员账号在 server 的 admin_credentials.txt 里"
echo "    Ctrl+C 退出"
echo "---"
exec "$VENV/bin/python" -m streamlit run admin.py --server.address 0.0.0.0 --server.port 8501 "$@"
