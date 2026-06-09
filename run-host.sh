#!/usr/bin/env bash
# Host performance baseline: run the SAME perf suite as the sandbox, but in a
# local Docker container with CPU/memory capped to the sandbox defaults.
#   - CPU/mem limits: 2 vCPU / 2048 MiB (override via SANDBOX_CPUS / SANDBOX_MEM_MB)
#   - Disk: host /tmp (bind-mounted), storage tests write to /tmp/perf-io
#   - Logs: host /tmp/result/<timestamp>/
# The container runs the suite immediately on start (BACKEND=local, no e2b SDK).
#
# Usage:
#   bash run-host.sh
#   SANDBOX_CPUS=2 SANDBOX_MEM_MB=1024 DURATION=10 bash run-host.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

CPUS="${SANDBOX_CPUS:-2}"
MEM_MB="${SANDBOX_MEM_MB:-2048}"
DURATION="${DURATION:-30}"

mkdir -p /tmp/result /tmp/perf-io

echo "==> building images (base perf-bench + perf-bench-host)"
docker build -t perf-bench      -f Dockerfile      .
docker build -t perf-bench-host -f Dockerfile.host .

echo "==> running host baseline: ${CPUS} vCPU / ${MEM_MB} MiB, duration=${DURATION}s, cases=${*:-<all>}"

# perf tracing of stress-ng (PERF_TRACE=1) needs perf permissions inside the
# container: grant CAP_SYS_ADMIN + CAP_PERFMON. If perf still fails on a locked-down
# host, swap these for --privileged. Note: an L1 cloud VM often has no hardware PMU,
# so sampling defaults to the software event cpu-clock (set in common.py) and works
# regardless; perf-stat hardware counters may show "<not supported>".
PERF_OPTS=()
if [ "${PERF_TRACE:-0}" != "0" ]; then
  echo "    perf tracing ON (mode=${PERF_MODE:-both} event=${PERF_EVENT:-cpu-clock}); adding --cap-add SYS_ADMIN,PERFMON"
  PERF_OPTS=(--cap-add SYS_ADMIN --cap-add PERFMON)
fi

# --cpuset-cpus pins to N cores so nproc==N (matches a 2-vCPU sandbox; --cpus
#   quota would leave nproc at the host count and skew A4/B10 scaling).
# --memory + equal --memory-swap caps RAM and disables swap.
# -v /tmp:/tmp gives the container the host disk for storage tests AND lands
#   results in host /tmp/result in one mount.
# --tmpfs /mnt/tmpfsbench gives the tmpfs cases (C12/C17) a tmpfs path without a
#   privileged container; the sandbox mounts the same path itself at run time.
docker run --rm \
  --cpuset-cpus="0-$((CPUS - 1))" \
  --memory="${MEM_MB}m" --memory-swap="${MEM_MB}m" \
  "${PERF_OPTS[@]}" \
  --tmpfs /mnt/tmpfsbench:rw,size=1g \
  -v /tmp:/tmp \
  -e DURATION="${DURATION}" \
  -e SANDBOX_CPUS="${CPUS}" -e SANDBOX_MEM_MB="${MEM_MB}" \
  -e PERF_TRACE -e PERF_MODE -e PERF_EVENT -e PERF_FREQ -e PERF_MATCH -e PERF_REPORT_LINES \
  perf-bench-host \
  python3 /test/runner/run_all.py "$@"

echo "==> done. latest batch:"
ls -1dt /tmp/result/*/ 2>/dev/null | head -1
