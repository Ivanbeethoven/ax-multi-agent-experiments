#!/bin/bash
# 检查实验机的磁盘落点是否符合 paper-design/EXPERIMENT-CONFIG.zh.md
# 用法: check-disk-layout.sh [work_root]   默认 /work
set -u
ROOT=${1:-/work}
FAIL=0

say()  { printf '%s\n' "$*"; }
ok()   { printf '  OK   %s\n' "$*"; }
bad()  { printf '  FAIL %s\n' "$*"; FAIL=1; }
warn() { printf '  WARN %s\n' "$*"; }

say "== 1. 每块盘的设备与能力 =="
for p in "$ROOT" "$ROOT"/d1 "$ROOT"/d2 "$ROOT"/d3 "$ROOT"/d4; do
  [ -e "$p" ] || { warn "$p 不存在"; continue; }
  dev=$(df --output=source "$p" 2>/dev/null | tail -1)
  size=$(df -h --output=size "$p" 2>/dev/null | tail -1 | tr -d ' ')
  say "  $p -> $dev ($size)"
done

say
say "== 2. 必须落在数据盘的重活目录 =="
SYSTEM_DEV=$(df --output=source / 2>/dev/null | tail -1)
say "  系统盘设备: $SYSTEM_DEV"

check_on_data() {
  local p=$1 label=$2
  [ -e "$p" ] || { warn "$label 不存在（$p）"; return; }
  local dev
  dev=$(df --output=source "$p" 2>/dev/null | tail -1)
  if [ "$dev" = "$SYSTEM_DEV" ]; then
    bad "$label 在系统盘（$p -> $dev）—— 会被 6,800 IOPS 卡住"
  else
    ok "$label 在数据盘（$p -> $dev）"
  fi
}

# 语料
for d in "$ROOT"/d*/corpus "$ROOT"/d*/*-corpus "$ROOT"/corpus; do
  [ -e "$d" ] && check_on_data "$d" "语料"
done

# fio 目标
for d in "$ROOT"/d*/fio* "$ROOT"/fio*; do
  [ -e "$d" ] && check_on_data "$d" "fio 目标"
done

# 客户端缓存
for d in "$ROOT"/d*/jfscache "$ROOT"/jfscache "$ROOT"/d*/cache "$ROOT"/cache; do
  [ -e "$d" ] && check_on_data "$d" "缓存目录"
done

# RustFS volume 必须在数据盘
for i in 1 2 3 4; do
  [ -e "$ROOT/d$i" ] && check_on_data "$ROOT/d$i" "RustFS volume d$i"
done

say
say "== 3. 系统盘上不该有的体积 =="
BIG=$(du -sm "$ROOT"/* 2>/dev/null | awk -v s="$SYSTEM_DEV" '$1 > 2048 {print}')
if [ -n "$BIG" ]; then
  while read -r mb path; do
    dev=$(df --output=source "$path" 2>/dev/null | tail -1)
    if [ "$dev" = "$SYSTEM_DEV" ]; then
      bad "$path 占 ${mb}MiB 且位于系统盘"
    fi
  done <<< "$BIG"
else
  ok "系统盘上没有 >2 GiB 的目录"
fi

say
if [ "$FAIL" -eq 0 ]; then
  say "结论: 通过"
else
  say "结论: 不通过 —— 把上面 FAIL 的目录迁到 $ROOT/d1..d4 后再跑"
fi
exit "$FAIL"
