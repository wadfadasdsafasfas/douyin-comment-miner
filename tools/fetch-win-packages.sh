#!/bin/bash
# 等 Windows 制品下载完成 → 校验 → 解包 → 规范命名 → 上传服务器 → 自检
# 用法：nohup bash tools/fetch-win-packages.sh > /tmp/winrelay.log 2>&1 &
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT=/tmp/winrelay
SSHPASS_FILE=/tmp/.tc_pw
SERVER=root@117.72.28.123
LOG=/tmp/winrelay.status
# 自动取最近一次成功构建的 windows-packages 制品（免得手改 ID 和字节数）
cd "$DIR"
RUN=$(gh run list --workflow=build-release.yml --limit 1 --json databaseId,status,conclusion \
      --jq '.[0]|select(.status=="completed" and .conclusion=="success")|.databaseId' 2>/dev/null | head -1)
[ -z "$RUN" ] && RUN=$(gh run list --workflow=build-release.yml --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null)
META=$(gh api "repos/$(gh repo view --json nameWithOwner -q .nameWithOwner)/actions/runs/$RUN/artifacts" \
       --jq '.artifacts[]|select(.name=="windows-packages")|"\(.id) \(.size_in_bytes)"' 2>/dev/null | head -1)
AID=$(echo "$META" | awk '{print $1}'); EXPECTED=$(echo "$META" | awk '{print $2}')
[ -z "$AID" ] && { echo "找不到 windows-packages 制品（CI 可能还没跑完）"; exit 1; }
ZIP=/tmp/winpkg-$RUN.zip
echo "run=$RUN artifact=$AID size=$EXPECTED"

TOK=$(gh auth token)
nohup curl -sL -C - --retry 20 --retry-delay 5 --retry-all-errors -H "Authorization: Bearer $TOK" \
  -o "$ZIP" "https://api.github.com/repos/$(gh repo view --json nameWithOwner -q .nameWithOwner)/actions/artifacts/$AID/zip" \
  > /dev/null 2>&1 &
DL=$!

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
if [ "$SIZE" -lt "$EXPECTED" ]; then kill $DL 2>/dev/null; say "超时：仍只有 $SIZE bytes，放弃"; exit 1; fi

# 2) 解包
OUT="/tmp/winrelay-$RUN"   # 每次 run 用独立目录，产物天然干净，不需要清理步骤
mkdir -p "$OUT"
unzip -oq "$ZIP" -d "$OUT" || { say "解包失败"; exit 1; }
SETUP=$(find "$OUT" -maxdepth 1 -name "*-setup.exe" | head -1)
PORT=$(find "$OUT" -maxdepth 1 -name "*.zip" ! -name "winpkg.zip" | head -1)
[ -z "$SETUP" ] && { say "找不到 setup.exe"; ls -la "$OUT"; exit 1; }
say "setup: $(basename "$SETUP")  $(stat -f%z "$SETUP") bytes"
say "portable 源: ${PORT:-未找到}"
kill $DL 2>/dev/null || true

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
