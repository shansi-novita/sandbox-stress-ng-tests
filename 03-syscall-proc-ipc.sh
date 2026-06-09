#!/usr/bin/env bash
# Phase 3 — virtualization overhead (the part stress-ng-throughput misses).
#   B1 syscall latency      (lmbench lat_syscall)
#   B2 process creation     (lmbench lat_proc + stress-ng fork/exec)
#   B3 context switch        (lmbench lat_ctx + stress-ng switch)
#   B4 thread create         (stress-ng pthread)
#   B5 IPC pipe              (lmbench bw_pipe/lat_pipe + stress-ng pipe)
#   B6 IPC futex             (stress-ng futex)
#   B7 IPC epoll             (stress-ng epoll)
#   B8 pagefault/mmap        (lmbench lat_pagefault + stress-ng mmap)
#   B9 scheduling jitter     (cyclictest / schbench)
#   B10 composite            (UnixBench)
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
[[ -f "${_PERF_ROOT}/.tools/env.sh" ]] && source "${_PERF_ROOT}/.tools/env.sh"
section "Phase 3: virtualization overhead"

# helper: pull "<label>: <number> <unit>" style latency from lmbench output (ns/us).
lmb_num() { grep -oE '[0-9]+\.[0-9]+' "$1" | head -1; }

# --- B1 syscall latency (lat_syscall null/read/write/stat/open) ----------------
if need lat_syscall B1; then
  for op in null read write stat open; do
    r="$(raw B1-syscall-$op)"
    lat_syscall "$op" > "$r" 2>&1 || true
    # output: "Simple <op>: <X> microseconds"
    us="$(grep -oE '[0-9]+\.[0-9]+' "$r" | head -1)"
    [[ -n "$us" ]] && metric "B" "B1-$op" "syscall-$op" "latency" \
      "$(awk -v u="$us" 'BEGIN{printf "%.0f", u*1000}')" "ns" "lat_syscall $op"
  done
fi

# --- B2 process creation -------------------------------------------------------
if need lat_proc B2; then
  for kind in fork exec shell; do
    r="$(raw B2-proc-$kind)"
    lat_proc "$kind" > "$r" 2>&1 || true
    us="$(grep -oE '[0-9]+\.[0-9]+' "$r" | head -1)"
    [[ -n "$us" ]] && metric "B" "B2-$kind" "proc-$kind" "latency" "${us}" "us" "lat_proc $kind"
  done
fi
if need stress-ng B2b; then
  r="$(raw B2-stressng-fork)"
  stress-ng --fork 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
  ops="$(grep -E ' fork ' "$r" | grep -oE '[0-9.]+' | sed -n '3p')"
  [[ -n "$ops" ]] && metric "B" "B2b" "proc-fork-throughput" "bogo_ops_per_s" "${ops}" "ops/s" "stress-ng fork"
fi

# --- B3 context switch ---------------------------------------------------------
if need lat_ctx B3; then
  r="$(raw B3-ctx)"
  # working-set sweep; lmbench prints "<size> <us>" rows.
  lat_ctx -s 0 2 4 8 16 2 4 8 16 > "$r" 2>&1 || lat_ctx 2 4 8 16 > "$r" 2>&1 || true
  us="$(awk 'NF==2{v=$2} END{print v}' "$r")"
  [[ -n "$us" ]] && metric "B" "B3" "ctx-switch" "latency" \
    "$(awk -v u="$us" 'BEGIN{printf "%.0f", u*1000}')" "ns" "lat_ctx sweep"
fi
if have stress-ng; then
  r="$(raw B3-stressng-switch)"
  stress-ng --switch 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
  ops="$(grep -E ' switch ' "$r" | grep -oE '[0-9.]+' | sed -n '3p')"
  [[ -n "$ops" ]] && metric "B" "B3b" "ctx-switch-throughput" "bogo_ops_per_s" "${ops}" "ops/s" "stress-ng switch"
fi

# --- B4 thread creation --------------------------------------------------------
if need stress-ng B4; then
  r="$(raw B4-pthread)"
  stress-ng --pthread 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
  ops="$(grep -E ' pthread ' "$r" | grep -oE '[0-9.]+' | sed -n '3p')"
  [[ -n "$ops" ]] && metric "B" "B4" "thread-create" "bogo_ops_per_s" "${ops}" "ops/s" "stress-ng pthread"
fi

# --- B5 IPC pipe ---------------------------------------------------------------
if need bw_pipe B5; then
  r="$(raw B5-bw_pipe)"
  bw_pipe > "$r" 2>&1 || true
  mb="$(grep -oE '[0-9]+\.[0-9]+' "$r" | tail -1)"
  [[ -n "$mb" ]] && metric "B" "B5" "ipc-pipe-bw" "bandwidth" "${mb}" "MB/s" "lmbench bw_pipe"
fi
if have stress-ng; then
  r="$(raw B5-stressng-pipe)"
  stress-ng --pipe 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
  ops="$(grep -E ' pipe ' "$r" | grep -oE '[0-9.]+' | sed -n '3p')"
  [[ -n "$ops" ]] && metric "B" "B5b" "ipc-pipe-throughput" "bogo_ops_per_s" "${ops}" "ops/s" "stress-ng pipe"
fi

# --- B6 futex / B7 epoll -------------------------------------------------------
for spec in "futex:B6" "epoll:B7"; do
  m="${spec%%:*}"; id="${spec##*:}"
  if have stress-ng; then
    r="$(raw ${id}-${m})"
    stress-ng --"$m" 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
    ops="$(grep -E " ${m} " "$r" | grep -oE '[0-9.]+' | sed -n '3p')"
    [[ -n "$ops" ]] && metric "B" "$id" "ipc-${m}" "bogo_ops_per_s" "${ops}" "ops/s" "stress-ng ${m}"
  fi
done

# --- B8 pagefault / mmap -------------------------------------------------------
if need lat_pagefault B8; then
  r="$(raw B8-pagefault)"
  # lat_pagefault needs a file argument; use a temp file.
  tf="$(mktemp)"; dd if=/dev/zero of="$tf" bs=1M count=64 >/dev/null 2>&1
  lat_pagefault "$tf" > "$r" 2>&1 || true
  us="$(grep -oE '[0-9]+\.[0-9]+' "$r" | head -1)"
  [[ -n "$us" ]] && metric "B" "B8" "pagefault" "latency" "${us}" "us" "lat_pagefault 64M"
  rm -f "$tf"
fi
if have stress-ng; then
  r="$(raw B8-stressng-mmap)"
  stress-ng --mmap 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
  ops="$(grep -E ' mmap ' "$r" | grep -oE '[0-9.]+' | sed -n '3p')"
  [[ -n "$ops" ]] && metric "B" "B8b" "mmap-throughput" "bogo_ops_per_s" "${ops}" "ops/s" "stress-ng mmap"
fi

# --- B9 scheduling jitter / wakeup latency -------------------------------------
if have cyclictest; then
  r="$(raw B9-cyclictest)"
  # short, unprivileged-friendly run; needs CAP for FIFO but falls back to default policy.
  cyclictest -l 100000 -q -D "${DURATION}" 2>&1 | tail -20 > "$r" || true
  avg="$(grep -oE 'Avg:[[:space:]]*[0-9]+' "$r" | grep -oE '[0-9]+' | head -1)"
  maxv="$(grep -oE 'Max:[[:space:]]*[0-9]+' "$r" | grep -oE '[0-9]+' | head -1)"
  [[ -n "$avg"  ]] && metric "B" "B9"  "sched-jitter-avg" "latency" "${avg}"  "us" "cyclictest"
  [[ -n "$maxv" ]] && metric "B" "B9b" "sched-jitter-max" "latency" "${maxv}" "us" "cyclictest"
elif have schbench; then
  r="$(raw B9-schbench)"
  schbench -m 2 -t 4 -r "${DURATION}" > "$r" 2>&1 || true
  p99="$(grep -iE '99.0th' "$r" | grep -oE '[0-9]+' | tail -1)"
  [[ -n "$p99" ]] && metric "B" "B9" "sched-wakeup-p99" "latency" "${p99}" "us" "schbench"
else
  warn "neither cyclictest nor schbench present — skipping B9"
  metric "skip" "B9" "sched-jitter" "status" "skipped" "missing:cyclictest/schbench" ""
fi

# --- B10 composite: UnixBench (single + multi) ---------------------------------
# Honor UNIXBENCH_DIR (set by the Docker image); fall back to the source-build path.
UB_DIR="${UNIXBENCH_DIR:-${_PERF_ROOT}/.tools/byte-unixbench/UnixBench}"
if [[ -x "${UB_DIR}/Run" ]]; then
  for c in 1 "$(nproc 2>/dev/null || echo 1)"; do
    r="$(raw B10-unixbench-c${c})"
    ( cd "$UB_DIR" && ./Run -c "$c" ) > "$r" 2>&1 || true
    score="$(grep -E 'System Benchmarks Index Score' "$r" | grep -oE '[0-9.]+' | tail -1)"
    [[ -n "$score" ]] && metric "B" "B10-c${c}" "unixbench" "index_score" "${score}" "score" "-c ${c}"
  done
else
  warn "UnixBench not built — skipping B10 (run 00-setup.sh)"
  metric "skip" "B10" "unixbench" "status" "skipped" "not-built" ""
fi

section "Phase 3: complete"
