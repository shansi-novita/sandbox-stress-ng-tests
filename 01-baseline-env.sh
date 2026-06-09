#!/usr/bin/env bash
# Phase 1 — capture the environment fingerprint for this run.
# Writes a human-readable env.txt AND records key facts as metrics so runs
# (bare-metal vs sandbox) can be compared apples-to-apples afterwards.
set -uo pipefail
source "$(dirname "$0")/lib/common.sh"
[[ -f "${_PERF_ROOT}/.tools/env.sh" ]] && source "${_PERF_ROOT}/.tools/env.sh"

section "Phase 1: environment fingerprint"
ENV_TXT="${RESULTS_DIR}/env.txt"

dump() { printf '\n### %s\n' "$1" >> "${ENV_TXT}"; shift; "$@" >> "${ENV_TXT}" 2>&1 || echo "(unavailable)" >> "${ENV_TXT}"; }

: > "${ENV_TXT}"
{
  echo "run-id: ${RUN_ID}"
  echo "captured: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >> "${ENV_TXT}"

dump "uname"            uname -a
dump "os-release"       cat /etc/os-release
dump "cpuinfo"          sh -c 'lscpu 2>/dev/null || cat /proc/cpuinfo'
dump "cpu-vendor-flags" sh -c "grep -m1 -E 'vendor_id|model name|flags' /proc/cpuinfo"
dump "meminfo"          sh -c 'head -5 /proc/meminfo'
dump "mounts"           sh -c 'mount | grep -E "ext4|overlay|tmpfs|9p|virtiofs" || mount'
dump "block-devices"    sh -c 'lsblk 2>/dev/null || cat /proc/partitions'
dump "filesystem-fs"    sh -c 'df -hT'
dump "kernel-cmdline"   cat /proc/cmdline
dump "scheduler"        sh -c 'for d in /sys/block/*/queue/scheduler; do echo "$d: $(cat $d 2>/dev/null)"; done'
dump "cgroup-version"   sh -c 'stat -fc %T /sys/fs/cgroup'
dump "cgroup-limits"    sh -c 'cat /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null'
dump "virtualization"   sh -c 'systemd-detect-virt 2>/dev/null || (grep -q hypervisor /proc/cpuinfo && echo "hypervisor flag present" || echo "unknown")'
dump "loadavg"          cat /proc/loadavg
dump "tool-versions"    sh -c 'for t in sysbench stress-ng fio ioping iperf3 netperf openssl 7z wrk lat_syscall; do printf "%-12s " "$t"; command -v $t >/dev/null && ($t --version 2>&1 | head -1) || echo "MISSING"; done'

# --- record machine-readable facts as metrics ---
NCPU="$(nproc 2>/dev/null || echo NA)"
MEM_KB="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo NA)"
VIRT="$(systemd-detect-virt 2>/dev/null || echo unknown)"
metric "env" "A0-env" "environment" "nproc"       "${NCPU}"   "cores" ""
metric "env" "A0-env" "environment" "mem_total"   "${MEM_KB}" "kB"    ""
metric "env" "A0-env" "environment" "virt"        "1"         "flag"  "${VIRT}"

log "environment fingerprint -> ${ENV_TXT}"
log "cpu=${NCPU} cores, mem=${MEM_KB} kB, virt=${VIRT}"
section "Phase 1 complete"
