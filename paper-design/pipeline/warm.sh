#!/usr/bin/env bash
# 下层预热执行器（W 口径入口）
#
# 目的：把「冷取下层 / 冷建 chunk 索引与 read plan」的一次性成本排除在正式测量之外。
# 判据：连续两遍预热读的吞吐差 < target（默认 10%）才认为热到位。
#
# 用法：
#   warm.sh --dir /mnt/native [--size 512m] [--numjobs 4] [--iodepth 32]
#           [--runtime 30] [--target 0.10] [--max-passes 4] [--out /tmp/warm.json]
#
# 退出码：0=已热到位；2=达到最大轮数仍未稳定；1=参数/依赖错误
set -euo pipefail

DIR=""; SIZE="512m"; NUMJOBS="4"; IODEPTH="32"; RUNTIME="30"; TARGET="0.10"; MAXP="4"; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --size) SIZE="$2"; shift 2 ;;
    --numjobs) NUMJOBS="$2"; shift 2 ;;
    --iodepth) IODEPTH="$2"; shift 2 ;;
    --runtime) RUNTIME="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --max-passes) MAXP="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

[ -n "$DIR" ] || { echo "--dir required" >&2; exit 1; }
[ -d "$DIR" ] || { echo "not a directory: $DIR" >&2; exit 1; }
command -v fio >/dev/null || { echo "fio not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
: > "$WORK/bw.txt"

pass=1
while [ "$pass" -le "$MAXP" ]; do
  fio --name=warm --directory="$DIR" --rw=randread --bs=4k --size="$SIZE" \
      --numjobs="$NUMJOBS" --ioengine=libaio --iodepth="$IODEPTH" --direct=1 \
      --runtime="$RUNTIME" --time_based --group_reporting \
      --output-format=json --output="$WORK/pass-$pass.json" >/dev/null
  bw=$(python3 - "$WORK/pass-$pass.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("%.6f" % (d["jobs"][0]["read"]["bw_bytes"]/1048576.0))
PY
)
  echo "$bw" >> "$WORK/bw.txt"
  echo "pass $pass: ${bw} MiB/s"

  if [ "$pass" -ge 2 ]; then
    status=$(python3 - "$WORK/bw.txt" "$TARGET" <<'PY'
import sys
bw=[float(x) for x in open(sys.argv[1]).read().split()]
target=float(sys.argv[2])
prev,cur=bw[-2],bw[-1]
delta=abs(cur-prev)/max(prev,1e-9)
print("WARM" if delta<target else "COLD")
print("%.4f" % delta, file=sys.stderr)
PY
)
  else
    status="COLD"
  fi

  if [ "$status" = "WARM" ]; then
    RESULT=$(python3 - "$WORK/bw.txt" "$TARGET" "$pass" "$DIR" "$OUT" <<'PY'
import json,sys
bw=[float(x) for x in open(sys.argv[1]).read().split()]
target=float(sys.argv[2]); passes=int(sys.argv[3]); d=sys.argv[4]; out=sys.argv[5] or ""
doc={
  "protocol": "W-lower-prewarmed",
  "dir": d,
  "passes": passes,
  "bw_mib_s": [round(x,3) for x in bw],
  "settled_delta_pct": round(abs(bw[-1]-bw[-2])/max(bw[-2],1e-9)*100,3),
  "target_pct": round(target*100,3),
  "status": "warm",
}
if out:
    open(out,"w").write(json.dumps(doc, ensure_ascii=False, indent=2))
print(json.dumps(doc, ensure_ascii=False))
PY
)
    echo "$RESULT"
    exit 0
  fi
  pass=$((pass+1))
done

RESULT=$(python3 - "$WORK/bw.txt" "$TARGET" "$MAXP" "$DIR" "$OUT" <<'PY'
import json,sys
bw=[float(x) for x in open(sys.argv[1]).read().split()]
target=float(sys.argv[2]); passes=int(sys.argv[3]); d=sys.argv[4]; out=sys.argv[5] or ""
doc={
  "protocol": "W-lower-prewarmed",
  "dir": d,
  "passes": passes,
  "bw_mib_s": [round(x,3) for x in bw],
  "settled_delta_pct": round(abs(bw[-1]-bw[-2])/max(bw[-2],1e-9)*100,3) if len(bw)>1 else None,
  "target_pct": round(target*100,3),
  "status": "not-settled",
}
if out:
    open(out,"w").write(json.dumps(doc, ensure_ascii=False, indent=2))
print(json.dumps(doc, ensure_ascii=False))
PY
)
echo "$RESULT"
echo "未在 $MAXP 轮内稳定；不要在此状态下的数值上做结论。" >&2
exit 2
