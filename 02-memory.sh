#!/usr/bin/env bash
# Phase 2 — Memory (A5 bandwidth, A6 latency curve, A7 random/TLB).
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
[[ -f "${_PERF_ROOT}/.tools/env.sh" ]] && source "${_PERF_ROOT}/.tools/env.sh"
section "Phase 2: Memory"

# --- A5 bandwidth: stress-ng stream (STREAM-like triad) ------------------------
if need stress-ng A5; then
  r="$(raw A5-stream)"
  stress-ng --stream 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
  # stress-ng reports a "memory rate" line for stream in MB per sec.
  val="$(grep -iE 'stream .*MB per sec|memory rate' "$r" | grep -oE '[0-9.]+' | tail -1)"
  metric "A" "A5" "mem-bandwidth-stream" "rate" "${val:-NA}" "MB/s" "stream triad"
fi
# Optional: sysbench memory as a cross-check.
if have sysbench; then
  r="$(raw A5-sysbench-mem)"
  sysbench memory --memory-block-size=1M --memory-total-size=10G --threads=1 run > "$r" 2>&1
  val="$(extract 'transferred \([0-9.]+ MiB/sec\)' "$r")"
  [[ -z "$val" ]] && val="$(grep -oE '[0-9.]+ MiB/sec' "$r" | grep -oE '[0-9.]+' | head -1)"
  metric "A" "A5b" "mem-bandwidth-sysbench" "rate" "${val:-NA}" "MiB/s" "1M block 10G total"
fi

# --- A6 latency curve: lmbench lat_mem_rd --------------------------------------
if need lat_mem_rd A6; then
  r="$(raw A6-lat_mem_rd)"
  # stride 128 across up to 256MB: output is "<size_MB> <latency_ns>" pairs.
  lat_mem_rd 256 128 > "$r" 2>&1
  # Smallest size ~ L1 latency; largest ~ DRAM latency.
  l1="$(awk 'NF==2{print $2; exit}' "$r")"
  dram="$(awk 'NF==2{v=$2} END{print v}' "$r")"
  [[ -n "$l1"   ]] && metric "A" "A6"  "mem-latency-l1"   "latency" "${l1}"   "ns" "smallest working set"
  [[ -n "$dram" ]] && metric "A" "A6b" "mem-latency-dram" "latency" "${dram}" "ns" "largest working set"
fi

# --- A7 random access / TLB: stress-ng tlb-shootdown ---------------------------
if need stress-ng A7; then
  r="$(raw A7-tlb)"
  stress-ng --tlb-shootdown 1 -t "${DURATION}" --metrics-brief > "$r" 2>&1
  val="$(grep -E 'tlb-shootdown' "$r" | grep -oE '[0-9.]+' | sed -n '3p')"
  metric "A" "A7" "mem-tlb-shootdown" "bogo_ops_per_s" "${val:-NA}" "ops/s" ""
fi

section "Phase 2: Memory complete"
