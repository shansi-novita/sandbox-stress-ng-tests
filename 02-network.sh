#!/usr/bin/env bash
# Phase 2 — Network (A13 bandwidth, A14 latency, A15 connection/DNS).
# Bandwidth/latency need a server peer; set IPERF_SERVER / NETPERF_SERVER to enable.
# DNS + an HTTP fetch latency run with no peer (uses public endpoints if reachable).
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
[[ -f "${_PERF_ROOT}/.tools/env.sh" ]] && source "${_PERF_ROOT}/.tools/env.sh"
section "Phase 2: Network"

# --- A13 bandwidth: iperf3 (needs `iperf3 -s` on IPERF_SERVER) ------------------
if [[ -n "${IPERF_SERVER}" ]]; then
  if need iperf3 A13; then
    r="$(raw A13-iperf3)"
    iperf3 -c "${IPERF_SERVER}" -t "${DURATION}" -J > "$r" 2>&1 || iperf3 -c "${IPERF_SERVER}" -t "${DURATION}" > "$r" 2>&1
    bps="$(grep -oE '"bits_per_second":[[:space:]]*[0-9.]+' "$r" | tail -1 | grep -oE '[0-9.]+')"
    if [[ -n "$bps" ]]; then
      metric "A" "A13" "net-bandwidth" "throughput" "$(awk -v b="$bps" 'BEGIN{printf "%.2f", b/1e9}')" "Gbps" "iperf3 -> ${IPERF_SERVER}"
    else
      val="$(grep -E 'receiver|sender' "$r" | grep -oE '[0-9.]+ [GM]bits/sec' | tail -1)"
      [[ -n "$val" ]] && metric "A" "A13" "net-bandwidth" "throughput" "${val%% *}" "${val##* }" "iperf3"
    fi
  fi
else
  warn "IPERF_SERVER not set — skipping A13 bandwidth"
  metric "skip" "A13" "net-bandwidth" "status" "skipped" "no-server" "set IPERF_SERVER"
fi

# --- A14 latency: netperf TCP_RR (needs netserver on NETPERF_SERVER) ------------
if [[ -n "${NETPERF_SERVER}" ]]; then
  if need netperf A14; then
    r="$(raw A14-netperf)"
    netperf -H "${NETPERF_SERVER}" -t TCP_RR -l "${DURATION}" > "$r" 2>&1
    # transactions/sec is the trailing number on the data line.
    txn="$(awk '/^[0-9]/{v=$NF} END{print v}' "$r")"
    [[ -n "$txn" ]] && metric "A" "A14" "net-latency-rr" "txn_per_s" "${txn}" "txn/s" "TCP_RR"
  fi
else
  warn "NETPERF_SERVER not set — falling back to ping if a target is reachable"
  if have ping; then
    r="$(raw A14-ping)"
    if ping -c 50 -i 0.2 8.8.8.8 > "$r" 2>&1; then
      avg="$(grep -oE 'min/avg/max[^=]*= [0-9.]+/[0-9.]+' "$r" | grep -oE '[0-9.]+' | sed -n '2p')"
      [[ -n "$avg" ]] && metric "A" "A14" "net-latency-ping" "rtt_avg" "${avg}" "ms" "ping 8.8.8.8 x50"
    else
      metric "skip" "A14" "net-latency" "status" "skipped" "no-egress" ""
    fi
  fi
fi

# --- A15 connection rate + DNS resolve -----------------------------------------
if have wrk && [[ -n "${WRK_TARGET:-}" ]]; then
  r="$(raw A15-wrk)"
  wrk -c100 -t4 -d"${DURATION}s" "${WRK_TARGET}" > "$r" 2>&1
  rps="$(grep -E 'Requests/sec' "$r" | grep -oE '[0-9.]+' | head -1)"
  [[ -n "$rps" ]] && metric "A" "A15" "net-http-rps" "requests_per_s" "${rps}" "req/s" "wrk c100 -> ${WRK_TARGET}"
else
  warn "WRK_TARGET not set — skipping A15 HTTP rps"
fi
if have dig; then
  r="$(raw A15-dns)"
  total=0; n=0
  for i in 1 2 3 4 5; do
    ms="$(dig +noall +stats example.com 2>/dev/null | grep -oE 'Query time: [0-9]+' | grep -oE '[0-9]+')"
    [[ -n "$ms" ]] && { total=$((total+ms)); n=$((n+1)); echo "query $i: ${ms} ms" >> "$r"; }
  done
  [[ "$n" -gt 0 ]] && metric "A" "A15b" "net-dns" "resolve_avg" "$((total/n))" "ms" "dig example.com x${n}"
fi

section "Phase 2: Network complete"
