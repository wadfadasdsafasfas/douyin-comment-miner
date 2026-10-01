#!/bin/bash
# Windows 制品并行分段下载：GitHub Actions 制品走 Azure Blob，支持 Range(206)，
# 单条连接被限速到 ~56KB/s，开 6 条并行能快好几倍。
# 产物：/tmp/winpkg-parallel.zip（大小与字节校验通过后再交给接力脚本的上传段）
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
RUN=${1:-36813956256}
AID=${2:-11140308096}
EXPECTED=${3:-297423330}
PARTS=${4:-6}
OUT=/tmp/winpkg-parallel.zip
STAGE=/tmp/winseg-$RUN
LOG=/tmp/windl.log

say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

API="https://api.github.com/repos/$(gh repo view --json nameWithOwner -q .nameWithOwner)/actions/artifacts/$AID/zip"
# 先解析出带签名的最终地址（每次重新取，避免 SAS 过期）
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' -H "Authorization: Bearer $(gh auth token)" "$API")
[ -z "$LOC" ] && { say "拿不到制品重定向地址，退出"; exit 1; }
say "制品地址已解析（长度 ${#LOC}）"

mkdir -p "$STAGE"
SEG=$(( EXPECTED / PARTS ))

pids=()
for i in $(seq 0 $((PARTS-1))); do
  s=$(( i * SEG ))
  e=$(( s + SEG - 1 )); [ $i -eq $((PARTS-1)) ] && e=$(( EXPECTED - 1 ))
  want=$(( e - s + 1 ))
  (
    for try in 1 2 3 4 5; do
      have=0
      [ -f "$STAGE/part$i" ] && have=$(stat -f%z "$STAGE/part$i")
      [ "$have" -ge "$want" ] && break
      curl -sL --fail -C - -r "$(( s + have ))-$e" --retry 8 --retry-delay 3 --retry-all-errors \
        -o "$STAGE/part$i" "$LOC" && true
      have=0; [ -f "$STAGE/part$i" ] && have=$(stat -f%z "$STAGE/part$i")
      [ "$have" -ge "$want" ] && break
      sleep 5
    done
  ) &
  pids+=($!)
  say "段 $i: $s - $e (${want} bytes)"
done

# 监控打印
(
  while :; do
    tot=$( (cd "$STAGE" && stat -f%z part* 2>/dev/null) | awk '{s+=$1} END {print s+0}')
    say "并行进度 ${tot}/${EXPECTED}"
    sleep 45
  done
) & MON=$!

for p in "${pids[@]}"; do wait "$p"; done
kill $MON 2>/dev/null || true

tot=$( (cd "$STAGE" && stat -f%z part* 2>/dev/null) | awk '{s+=$1} END {print s+0}')
say "分片合计 ${tot}/${EXPECTED}"
[ "$tot" -lt "$EXPECTED" ] && { say "分片不完整，放弃并行（保留原单线下载）"; exit 2; }

cat $(for i in $(seq 0 $((PARTS-1))); do printf '%s ' "$STAGE/part$i"; done) > "$OUT"
sz=$(stat -f%z "$OUT")
say "合并完成 $sz bytes → $OUT"
if unzip -tq "$OUT" >/dev/null 2>&1; then say "✓ zip 完整性校验通过"; else say "✗ zip 校验失败"; exit 3; fi
say "DONE"
