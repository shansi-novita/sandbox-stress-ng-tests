#!/usr/bin/env bash
# Phase 2 — Storage (A8 seq IO, A9 random IO, A10 metadata/small files,
#                     A11 fsync, A12 IO latency).
# All tests write under STORAGE_DIR (default /tmp/perf-io). --direct=1 bypasses
# page cache to measure the real backend (NBD/ext4); cleaned up at the end.
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
[[ -f "${_PERF_ROOT}/.tools/env.sh" ]] && source "${_PERF_ROOT}/.tools/env.sh"
section "Phase 2: Storage  (dir=${STORAGE_DIR})"
mkdir -p "${STORAGE_DIR}"

# fio common args; --output-format=json so we can parse reliably.
FIO_COMMON=(--directory="${STORAGE_DIR}" --output-format=json --group_reporting=1 --runtime="${DURATION}" --time_based=1)

# helper: pull a nested value from fio json with python (preferred) or grep fallback.
fio_json() { # <file> <json-path e.g. jobs[0].read.bw>
  local f="$1" path="$2"
  if have python3; then
    python3 - "$f" "$path" <<'PY' 2>/dev/null
import json,sys,re
f,path=sys.argv[1],sys.argv[2]
try:
    d=json.load(open(f))
    for tok in re.findall(r'(\w+)|\[(\d+)\]', path):
        k,i=tok
        d=d[k] if k else d[int(i)]
    print(d)
except Exception:
    pass
PY
  fi
}

if need fio A8; then
  # --- A8 sequential read + write (bs=1M) ---
  for mode in read write; do
    r="$(raw A8-seq-$mode)"
    fio "${FIO_COMMON[@]}" --name="seq$mode" --rw="$mode" --bs=1M --size=1G --direct=1 --numjobs=1 > "$r" 2>&1
    bw="$(fio_json "$r" "jobs[0].$mode.bw")"   # KiB/s
    [[ -n "$bw" ]] && metric "A" "A8-$mode" "storage-seq-$mode" "bandwidth" \
      "$(awk -v b="$bw" 'BEGIN{printf "%.1f", b/1024}')" "MB/s" "bs=1M direct"
  done

  # --- A9 random read/write (4k, iodepth=32) ---
  r="$(raw A9-randrw)"
  fio "${FIO_COMMON[@]}" --name=randrw --rw=randrw --bs=4k --iodepth=32 --ioengine=libaio \
      --size=1G --direct=1 --numjobs=1 > "$r" 2>&1
  for op in read write; do
    iops="$(fio_json "$r" "jobs[0].$op.iops")"
    p99="$(fio_json "$r" "jobs[0].$op.clat_ns.percentile.99.000000")"
    [[ -n "$iops" ]] && metric "A" "A9-$op" "storage-rand-$op" "iops" \
      "$(printf '%.0f' "$iops" 2>/dev/null || echo "$iops")" "IOPS" "4k qd32"
    [[ -n "$p99" ]] && metric "A" "A9-$op-p99" "storage-rand-$op" "lat_p99" \
      "$(awk -v n="$p99" 'BEGIN{printf "%.1f", n/1000}')" "us" "4k qd32"
  done

  # --- A11 fsync-heavy write (durability amplification) ---
  r="$(raw A11-fsync)"
  fio "${FIO_COMMON[@]}" --name=fsync --rw=write --bs=4k --fsync=1 --size=512M --direct=0 --numjobs=1 > "$r" 2>&1
  iops="$(fio_json "$r" "jobs[0].write.iops")"
  [[ -n "$iops" ]] && metric "A" "A11" "storage-fsync" "iops" \
    "$(printf '%.0f' "$iops" 2>/dev/null || echo "$iops")" "IOPS" "4k fsync=1"
fi

# --- A10 metadata / small files: real tar-extract of many small files ----------
# Build a tarball of ~5000 tiny files, then time extraction (mkdir+create+write storm).
r="$(raw A10-smallfiles)"
SF_DIR="${STORAGE_DIR}/smallfiles"
{
  rm -rf "$SF_DIR" && mkdir -p "$SF_DIR/src"
  ( cd "$SF_DIR/src" && for i in $(seq 1 5000); do echo "x" > "f$i"; done )
  tar cf "$SF_DIR/sf.tar" -C "$SF_DIR/src" .
  sync
  rm -rf "$SF_DIR/out" && mkdir -p "$SF_DIR/out"
  start=$(date +%s.%N)
  tar xf "$SF_DIR/sf.tar" -C "$SF_DIR/out"
  sync
  end=$(date +%s.%N)
  echo "extract_seconds=$(awk -v s="$start" -v e="$end" 'BEGIN{printf "%.3f", e-s}')"
} > "$r" 2>&1
secs="$(extract 'extract_seconds=[0-9.]+' "$r")"
if [[ -n "$secs" && "$secs" != "0" ]]; then
  fps="$(awk -v s="$secs" 'BEGIN{printf "%.0f", 5000/s}')"
  metric "A" "A10" "storage-smallfiles" "files_per_s" "${fps}" "files/s" "5000-file tar x"
fi

# --- A12 IO latency: ioping ----------------------------------------------------
if need ioping A12; then
  r="$(raw A12-ioping)"
  ioping -c 30 -D "${STORAGE_DIR}" > "$r" 2>&1
  # summary line: "min/avg/max/mdev = a / b / c / d"
  avg="$(grep -E 'min/avg/max' "$r" | sed -E 's#.*= *[0-9.]+ [a-z]+ */ *([0-9.]+) ([a-z]+).*#\1 \2#')"
  [[ -n "$avg" ]] && metric "A" "A12" "storage-io-latency" "avg" "${avg%% *}" "${avg##* }" "ioping -D c=30"
fi

# cleanup
rm -rf "${STORAGE_DIR}/smallfiles" "${STORAGE_DIR}"/seq* "${STORAGE_DIR}"/randrw* "${STORAGE_DIR}"/fsync* 2>/dev/null || true
section "Phase 2: Storage complete"
