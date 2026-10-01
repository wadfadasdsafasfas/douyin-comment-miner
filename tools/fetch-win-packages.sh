#!/bin/bash
# 等 Windows 制品下载完成 → 校验 → 解包 → 规范命名 → 上传服务器 → 自检
# 用法：nohup bash tools/fetch-win-packages.sh > /tmp/winrelay.log 2>&1 &
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
ZIP=/tmp/winpkg.zip
OUT=/tmp/winrelay
SSHPASS_FILE=/tmp/.tc_pw
SERVER=root@117.72.28.123
EXPECTED=297424245          # CI 报告的制品字节数，用于判断下载是否完整
LOG=/tmp/winrelay.status

say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

: > "$LOG"
say "开始等待下载完成（目标 ${EXPECTED} bytes）"

# 1) 轮询直到 zip 完整且能通过校验
for i in $(seq 1 320); do
  SIZE=$(stat -f%z "$ZIP" 2>/dev/null || echo 0)
  if [ "$SIZE" -ge "$EXPECTED" ]; then
    if unzip -tq "$ZIP" >/dev/null 2>&1; then say "下载完整且校验通过（$SIZE bytes）"; break; fi
  fi
  [ $((i % 10)) -eq 0 ] && say "进度 ${SIZE}/${EXPECTED}"
  sleep 30
done

SIZE=$(stat -f%z "$ZIP" 2>/dev/null || echo 0)
if [ "$SIZE" -lt "$EXPECTED" ]; then say "超时：仍只有 $SIZE bytes，放弃"; exit 1; fi

# 2) 解包
mkdir -p "$OUT"; rm -rf "$OUT"/* 2>/dev/null
unzip -oq "$ZIP" -d "$OUT" || { say "解包失败"; exit 1; }
SETUP=$(find "$OUT" -maxdepth 1 -name "*-setup.exe" | head -1)
PORT=$(find "$OUT" -maxdepth 1 -name "*.zip" ! -name "winpkg.zip" | head -1)
[ -z "$SETUP" ] && { say "找不到 setup.exe"; ls -la "$OUT"; exit 1; }
say "setup: $(basename "$SETUP")  $(stat -f%z "$SETUP") bytes"
say "portable 源: ${PORT:-未找到}"

# 3) 规范命名，与官网链接和 windows_url 对齐
cp -f "$SETUP" "$OUT/TingChao-windows-v1.0.0-setup.exe"
[ -n "$PORT" ] && cp -f "$PORT" "$OUT/TingChao-windows-v1.0.0-portable.zip"

export SSHPASS="$(cat "$SSHPASS_FILE" 2>/dev/null)"
if [ -z "${SSHPASS:-}" ]; then say "缺少服务器密码文件 $SSHPASS_FILE，停止"; exit 1; fi

# 4) 上传（先传小的 setup，再传 portable）
say "上传 setup.exe …"
sshpass -e scp -o StrictHostKeyChecking=no "$OUT/TingChao-windows-v1.0.0-setup.exe" "$SERVER:/opt/app/downloads/" \
  && say "✓ setup 已上传" || { say "setup 上传失败"; exit 1; }

if [ -n "$PORT" ]; then
  say "上传 portable.zip …"
  sshpass -e scp -o StrictHostKeyChecking=no "$OUT/TingChao-windows-v1.0.0-portable.zip" "$SERVER:/opt/app/downloads/" \
    && say "✓ portable 已上传" || say "portable 上传失败"
fi

# 5) 自检：官网两个链接应转为 200
sleep 3
for u in TingChao-windows-v1.0.0-setup.exe TingChao-windows-v1.0.0-portable.zip; do
  C=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -I "http://117.72.28.123/downloads/$u")
  say "  /downloads/$u → $C"
done
say "全部完成"
