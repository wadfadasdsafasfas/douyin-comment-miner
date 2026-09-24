#!/bin/bash
# 本地运行（macOS）—— 用 Homebrew Python 3.12 + 专用虚拟环境，首次自动装依赖
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
PY=/opt/homebrew/bin/python3.12
if [ ! -x "$PY" ]; then
  echo "没找到 $PY，请先： brew install python@3.12 python-tk@3.12"
  exit 1
fi
if [ ! -x "./.venv/bin/python" ]; then
  echo "首次运行：创建虚拟环境 .venv …"
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ customtkinter playwright openpyxl
fi
echo "启动界面…"
exec ./.venv/bin/python douyin_miner_gui.py
