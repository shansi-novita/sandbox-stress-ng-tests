#!/usr/bin/env bash
# Drive Phases 1-3 in order and print a summary table.
# All phases share one RUN_ID, so results land in a single results/<run-id>/ dir.
#
# Usage:
#   ./run-all.sh                       # full run (setup + baseline + phase 2 + 3)
#   SKIP_SETUP=1 ./run-all.sh          # skip tool install (tools already present)
#   DURATION=10 ./run-all.sh           # shorter per-test duration (smoke test)
#   LABEL=baremetal ./run-all.sh       # tag this run (for bare-metal vs sandbox diff)
#   IPERF_SERVER=10.0.0.5 ./run-all.sh # enable network bandwidth tests
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# Establish a single shared RUN_ID for all phases.
export RUN_ID="${RUN_ID:-$(date +%Y%m%d-%H%M%S)-${LABEL:-run}}"
source "${HERE}/lib/common.sh"

START="$(date +%s)"
log "==== sandbox perf run START  id=${RUN_ID}  label=${LABEL:-none} ===="

run_phase() {
  local script="$1"; shift
  log ">>> ${script}"
  if bash "${HERE}/${script}"; then
    log "<<< ${script} ok"
  else
    warn "<<< ${script} exited non-zero (continuing)"
  fi
}

[[ "${SKIP_SETUP:-0}" == "1" ]] || run_phase 00-setup.sh
run_phase 01-baseline-env.sh
run_phase 02-cpu.sh
run_phase 02-memory.sh
run_phase 02-storage.sh
run_phase 02-network.sh
run_phase 03-syscall-proc-ipc.sh

END="$(date +%s)"
section "SUMMARY  (id=${RUN_ID}, ${LABEL:-none}, $((END-START))s)"

# Pretty-print the metrics CSV (skip 'skip'/'env' rows in the headline table).
if have column; then
  { head -1 "${METRICS_CSV}"; grep -vE '^(skip|env)' "${METRICS_CSV}" | tail -n +2; } \
    | column -t -s, >&2
else
  cat "${METRICS_CSV}" >&2
fi

# Note any skipped tests so gaps are visible, not silent.
skipped="$(grep -c '^skip' "${METRICS_CSV}" 2>/dev/null || echo 0)"
[[ "${skipped}" -gt 0 ]] && warn "${skipped} test(s) skipped (missing tools / no server) — see metrics.csv"

log "results dir : ${RESULTS_DIR}"
log "metrics csv : ${METRICS_CSV}"
log "raw outputs : ${RAW_DIR}/"
log "env finger. : ${RESULTS_DIR}/env.txt"
echo
echo "To compare two runs (e.g. bare-metal vs sandbox):"
echo "  diff <(sort results/<baremetal-id>/metrics.csv) <(sort results/<sandbox-id>/metrics.csv)"
log "==== sandbox perf run DONE ===="
