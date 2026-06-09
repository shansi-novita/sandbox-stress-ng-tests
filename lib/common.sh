#!/usr/bin/env bash
# Shared harness for sandbox performance benchmarks.
# Source this from every phase script:  source "$(dirname "$0")/lib/common.sh"
#
# Provides:
#   - RESULTS_DIR / RAW_DIR / METRICS_CSV layout (one dir per run-id)
#   - log / warn / err            structured stderr logging
#   - have <tool>                 returns 0 if tool is on PATH
#   - need <tool> [test_id]       skip helper: logs + returns 1 if missing
#   - metric <phase> <id> <name> <metric> <value> <unit> [note]   append a CSV row
#   - raw <test_id>               path to that test's raw-output file
#   - section <title>            visual divider in the log

set -uo pipefail

# --- run identity & layout ---------------------------------------------------
# RUN_ID can be passed in by the orchestrator so all phases share one dir.
: "${RUN_ID:=$(date +%Y%m%d-%H%M%S)-$(hostname -s 2>/dev/null || echo host)}"
# Default results root sits next to the perf scripts.
_PERF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${RESULTS_ROOT:=${_PERF_ROOT}/results}"
RESULTS_DIR="${RESULTS_ROOT}/${RUN_ID}"
RAW_DIR="${RESULTS_DIR}/raw"
METRICS_CSV="${RESULTS_DIR}/metrics.csv"

mkdir -p "${RAW_DIR}"

# CSV header (write once).
if [[ ! -f "${METRICS_CSV}" ]]; then
  echo "phase,test_id,name,metric,value,unit,note" > "${METRICS_CSV}"
fi

# --- tunables (override via env) --------------------------------------------
: "${DURATION:=30}"          # seconds per stress-ng-style test
: "${STORAGE_DIR:=/tmp/perf-io}"   # where fio/ioping write their test files
: "${IPERF_SERVER:=}"        # set to run network bandwidth tests (A13)
: "${NETPERF_SERVER:=}"      # set to run network latency tests (A14)

# --- logging -----------------------------------------------------------------
_ts() { date +%H:%M:%S; }
log()  { printf '[%s] %s\n'      "$(_ts)" "$*" >&2; }
warn() { printf '[%s] WARN: %s\n' "$(_ts)" "$*" >&2; }
err()  { printf '[%s] ERR:  %s\n' "$(_ts)" "$*" >&2; }
section() {
  printf '\n========== %s ==========\n' "$*" >&2
}

# --- tool detection ----------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

# need <tool> [test_id] — returns 1 (skip) and logs a SKIP metric if absent.
need() {
  local tool="$1" id="${2:-}"
  if have "$tool"; then
    return 0
  fi
  warn "missing tool '${tool}' — skipping ${id:-test}"
  [[ -n "${id}" ]] && metric "skip" "${id}" "${id}" "status" "skipped" "missing:${tool}" ""
  return 1
}

# --- result emission ---------------------------------------------------------
# raw <test_id> -> absolute path of that test's raw output file.
raw() { echo "${RAW_DIR}/$1.txt"; }

# metric phase test_id name metric value unit [note]
# Commas in fields are replaced with ';' to keep the CSV simple.
metric() {
  local phase="$1" id="$2" name="$3" m="$4" val="$5" unit="$6" note="${7:-}"
  local clean
  clean() { printf '%s' "$1" | tr ',\n' ';;'; }
  printf '%s,%s,%s,%s,%s,%s,%s\n' \
    "$(clean "$phase")" "$(clean "$id")" "$(clean "$name")" \
    "$(clean "$m")" "$(clean "$val")" "$(clean "$unit")" "$(clean "$note")" \
    >> "${METRICS_CSV}"
  log "  -> ${id} ${m}=${val} ${unit}"
}

# extract <regex-with-one-capture-group> <file>  -> first capture or empty
# Convenience for pulling a number out of a tool's raw output.
extract() {
  local re="$1" file="$2"
  grep -oE "$re" "$file" 2>/dev/null | head -1 | grep -oE '[0-9]+\.?[0-9]*' | head -1
}

log "run-id=${RUN_ID}  results=${RESULTS_DIR}  duration=${DURATION}s"
