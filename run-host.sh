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

echo "==> running host baseline: ${CPUS} vCPU / ${MEM_MB} MiB, duration=${DURATION}s"
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
  --tmpfs /mnt/tmpfsbench:rw,size=1g \
  -v /tmp:/tmp \
  -e DURATION="${DURATION}" \
  -e SANDBOX_CPUS="${CPUS}" -e SANDBOX_MEM_MB="${MEM_MB}" \
  perf-bench-host

echo "==> done. latest batch:"
ls -1dt /tmp/result/*/ 2>/dev/null | head -1
