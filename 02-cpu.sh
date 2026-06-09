#!/usr/bin/env bash
# Phase 2 — CPU (A1 integer, A2 float, A3 crypto, A4 multicore scaling).
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
[[ -f "${_PERF_ROOT}/.tools/env.sh" ]] && source "${_PERF_ROOT}/.tools/env.sh"
section "Phase 2: CPU"
NCPU="$(nproc 2>/dev/null || echo 1)"

# --- A1 integer: sysbench prime + 7z compression --------------------------------
if need sysbench A1; then
  r="$(raw A1-sysbench-cpu)"
  sysbench cpu --cpu-max-prime=20000 --threads=1 --time="${DURATION}" run > "$r" 2>&1
  val="$(extract 'events per second:[[:space:]]*[0-9.]+' "$r")"
  metric "A" "A1" "cpu-int-sysbench" "events_per_s" "${val:-NA}" "ops/s" "prime=20000 t=1"
fi
if need 7z A1b; then
  r="$(raw A1-7z)"
  7z b > "$r" 2>&1
  # "Tot:" line reports combined MIPS rating in the last column.
  val="$(grep -E '^Tot:' "$r" | awk '{print $NF}' | head -1)"
  metric "A" "A1b" "cpu-int-7zip" "rating" "${val:-NA}" "MIPS" "7z b"
fi

# --- A2 float: stress-ng matrixprod + numpy GEMM (if python) --------------------
if need stress-ng A2; then
  r="$(raw A2-stressng-matrixprod)"
  stress-ng --cpu 1 --cpu-method matrixprod -t "${DURATION}" --metrics-brief > "$r" 2>&1
  val="$(grep -E 'cpu .* bogo ops/s' "$r" | awk '{print $(NF)}' | head -1)"
  [[ -z "$val" ]] && val="$(extract 'matrixprod[[:space:]]+[0-9]+[[:space:]]+[0-9.]+[[:space:]]+[0-9.]+[[:space:]]+[0-9.]+[[:space:]]+[0-9.]+' "$r")"
  metric "A" "A2" "cpu-float-matrixprod" "bogo_ops_per_s" "${val:-NA}" "ops/s" ""
fi
if have python3; then
  r="$(raw A2-numpy-gemm)"
  python3 - > "$r" 2>&1 <<'PY' || true
import time
try:
    import numpy as np
    n=2048; a=np.random.rand(n,n); b=np.random.rand(n,n)
    a@b  # warmup
    t=time.time(); a@b; dt=time.time()-t
    print(f"gflops={2*n**3/dt/1e9:.2f}")
except Exception as e:
    print(f"numpy_unavailable: {e}")
PY
  gf="$(extract 'gflops=[0-9.]+' "$r")"
  [[ -n "$gf" ]] && metric "A" "A2b" "cpu-float-numpy" "gflops" "${gf}" "GFLOPS" "2048x2048 GEMM"
fi

# --- A3 crypto: openssl AES + SHA + RSA ----------------------------------------
if need openssl A3; then
  r="$(raw A3-openssl)"
  { openssl speed -elapsed -evp aes-256-gcm 2>&1; echo "---"; \
    openssl speed -elapsed sha256 2>&1;          echo "---"; \
    openssl speed -elapsed rsa2048 2>&1; } > "$r"
  aes="$(grep -E 'aes-256-gcm' "$r" | awk '{print $NF}' | head -1)"
  [[ -n "$aes" ]] && metric "A" "A3" "crypto-aes" "throughput" "${aes}" "k/s" "aes-256-gcm 16384-block"
  rsa="$(grep -E 'rsa[[:space:]]*2048' "$r" | awk '{print $(NF-1)}' | head -1)"
  [[ -n "$rsa" ]] && metric "A" "A3b" "crypto-rsa" "sign_per_s" "${rsa}" "sign/s" "rsa2048"
fi

# --- A4 multicore scaling: sysbench at 1..nproc --------------------------------
if have sysbench; then
  r="$(raw A4-scaling)"; : > "$r"
  base=""
  # sample thread counts: 1, 2, half, full
  for t in $(printf '%s\n' 1 2 $((NCPU/2)) "$NCPU" | sort -un | awk '$1>=1'); do
    out="$(sysbench cpu --cpu-max-prime=20000 --threads="$t" --time="${DURATION}" run 2>&1)"
    eps="$(printf '%s' "$out" | grep -oE 'events per second:[[:space:]]*[0-9.]+' | grep -oE '[0-9.]+')"
    echo "threads=$t events_per_s=$eps" >> "$r"
    [[ "$t" == "1" ]] && base="$eps"
    if [[ -n "$eps" && -n "$base" && "$base" != "0" ]]; then
      eff="$(awk -v e="$eps" -v b="$base" -v t="$t" 'BEGIN{printf "%.1f", (e/b/t)*100}')"
      metric "A" "A4" "cpu-scaling" "efficiency_t${t}" "${eff}" "%" "threads=${t}"
    fi
  done
fi

section "Phase 2: CPU complete"
