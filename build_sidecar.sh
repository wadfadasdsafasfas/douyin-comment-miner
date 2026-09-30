#!/bin/bash
# 把本地 sidecar 打成单目录二进制，供 electron-builder 放进 Resources/sidecar/
# 产物：dist-sidecar/tingchao-sidecar/tingchao-sidecar      (macOS/Linux)
#       dist-sidecar/tingchao-sidecar/tingchao-sidecar.exe  (Windows，需在 Windows/CI 上执行)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# 依次尝试：项目 venv（mac/linux 与 Windows 两种布局）→ 系统 python
PY=""
for c in "./.venv/bin/python" "./.venv/Scripts/python.exe" "python3" "python"; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "[!] 找不到可用的 Python"; exit 1; }
echo "使用 Python: $PY"

"$PY" -m pip show pyinstaller >/dev/null 2>&1 || \
  "$PY" -m pip install -i https://mirrors.aliyun.com/pypi/simple/ pyinstaller

NAME="tingchao-sidecar"
SEP=":"
[ "$(uname -s)" = "Windows_NT" ] && SEP=";"

# PyInstaller 的 -m 只接受脚本文件，因此先生成一个入口脚本
cat > _sidecar_entry.py <<'ENTRY'
"""sidecar 打包入口：等价于 python -m tingchao.local_api"""
from tingchao.local_api import main

if __name__ == "__main__":
    main()
ENTRY

rm -rf build "$NAME".spec dist-sidecar 2>/dev/null || true
mkdir -p dist-sidecar

"$PY" -m PyInstaller --noconfirm --clean \
  --name "$NAME" \
  --onedir --console \
  --distpath dist-sidecar \
  --workpath build/sidecar \
  --specpath build/sidecar \
  --paths "$PWD" \
  --collect-all uvicorn \
  --collect-submodules fastapi \
  --hidden-import tingchao.local_api \
  --hidden-import tingchao.crawler \
  --hidden-import tingchao.leads_db \
  --hidden-import douyin_miner \
  --hidden-import license \
  --hidden-import server_url \
  --add-data "$PWD/tingchao/web${SEP}tingchao/web" \
  --add-data "$PWD/server_url.py${SEP}." \
  --add-data "$PWD/douyin_miner.py${SEP}." \
  --add-data "$PWD/license.py${SEP}." \
  _sidecar_entry.py

rm -f _sidecar_entry.py

echo
echo "✓ 产物：dist-sidecar/$NAME/$NAME"
echo "  冒烟测试：./dist-sidecar/$NAME/$NAME --port 8799 &  curl http://127.0.0.1:8799/api/health"
echo "  下一步：cd electron && npm run dist:mac"
