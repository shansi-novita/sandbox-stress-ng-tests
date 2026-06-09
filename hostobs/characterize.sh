#!/usr/bin/env bash
# characterize.sh — one-shot virtualization characterization of the L1 host
# (the VM where the orchestrator + Firecracker run). Run this ONCE as root on L1
# before a perf batch; its output frames how every later number is interpreted.
#
# The sandbox (L2) is a Firecracker microVM nested inside this VM (L1), which is
# itself a guest of the cloud host (L0). What this captures:
#   - Is L1 actually nested-virt-capable (vmx/svm exposed, kvm nested=Y)?
#   - EPT/NPT + APICv state — these dominate nested I/O and interrupt cost.
#   - /dev/kvm presence, clocksource, and an L1 steal baseline.
# It also prints the commands to run INSIDE a sandbox (L2) for the matching guest
# view. No changes are made; everything here is read-only.
#
# Usage:  sudo bash characterize.sh [out_file]
#         (defaults to printing to stdout; pass a path to also save a copy)

set -u

OUT="${1:-}"
emit() { if [ -n "$OUT" ]; then echo "$@" | tee -a "$OUT"; else echo "$@"; fi; }
run() {  # run() "label" cmd...   — print label then command output (stderr folded in)
  local label="$1"; shift
  emit ""
  emit "## $label"
  emit "\$ $*"
  local o; o="$("$@" 2>&1)"
  emit "${o:-(no output)}"
}

[ -n "$OUT" ] && : > "$OUT"
emit "############################################################"
emit "# L1 host virtualization characterization"
emit "# date : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
emit "# host : $(hostname 2>/dev/null)"
emit "############################################################"

# --- 1. What kind of machine is L1? -----------------------------------------
run "systemd-detect-virt (what hypervisor is L1 running under?)" \
    sh -c 'command -v systemd-detect-virt >/dev/null && systemd-detect-virt || echo "(systemd-detect-virt unavailable)"'

run "lscpu — model + Hypervisor vendor + Virtualization" \
    sh -c 'lscpu 2>/dev/null | grep -iE "model name|hypervisor|virtualization|vendor id|^cpu\(s\)|flags" || echo "(lscpu unavailable)"'

# vmx (Intel) / svm (AMD) in L1 cpuinfo => L1 exposes hardware virt to L2 = nested-capable.
run "Nested capability — vmx/svm in /proc/cpuinfo" \
    sh -c 'if grep -qwE "vmx|svm" /proc/cpuinfo; then echo "PRESENT (L1 exposes hardware virt -> nested OK):"; grep -m1 -owE "vmx|svm" /proc/cpuinfo; else echo "ABSENT (no vmx/svm — Firecracker would need software emulation / cannot use KVM)"; fi'

# --- 2. KVM nested-virt knobs (the cost-defining ones) ----------------------
# EPT/NPT (two-dimensional paging) and APICv/AVIC (hardware virtual interrupts)
# decide how expensive L2's memory faults and interrupts are under nesting.
for mod in kvm_intel kvm_amd; do
  base="/sys/module/$mod/parameters"
  [ -d "$base" ] || continue
  emit ""
  emit "## KVM module: $mod parameters (the nested-cost knobs)"
  for p in nested ept npt enable_apicv avic unrestricted_guest; do
    [ -f "$base/$p" ] && emit "  $p = $(cat "$base/$p" 2>/dev/null)"
  done
done

run "/dev/kvm present? (Firecracker requires it)" \
    sh -c 'ls -l /dev/kvm 2>&1 || echo "(/dev/kvm MISSING — KVM unavailable)"'

# --- 3. Time + steal baseline -----------------------------------------------
run "clocksource (kvm-clock under virt is normal)" \
    sh -c 'cat /sys/devices/system/clocksource/clocksource0/current_clocksource 2>/dev/null; echo "available:"; cat /sys/devices/system/clocksource/clocksource0/available_clocksource 2>/dev/null'

# L1 steal baseline: if this moves while idle, L0 is already preempting L1, which
# stacks on top of any nested cost. Sample two /proc/stat snapshots 3s apart.
emit ""
emit "## L1 steal baseline (idle, 3s window) — steal field 8 of the cpu line"
S0=$(awk '/^cpu /{print $9}' /proc/stat); T0=$(awk '/^cpu /{s=0;for(i=2;i<=9;i++)s+=$i;print s}' /proc/stat)
sleep 3
S1=$(awk '/^cpu /{print $9}' /proc/stat); T1=$(awk '/^cpu /{s=0;for(i=2;i<=9;i++)s+=$i;print s}' /proc/stat)
if [ "${T1:-0}" -gt "${T0:-0}" ]; then
  emit "  idle steal = $(awk "BEGIN{printf \"%.3f%%\", 100*($S1-$S0)/($T1-$T0)}") over 3s (want ~0%)"
else
  emit "  (could not compute steal delta)"
fi

# --- 4. perf / debugfs availability for observe.py --------------------------
run "perf_event_paranoid (perf kvm stat needs this low / root)" \
    sh -c 'cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "(n/a)"'
run "KVM debugfs present? (observe.py reads per-VM exit counters here)" \
    sh -c 'if [ -d /sys/kernel/debug/kvm ]; then echo "yes: /sys/kernel/debug/kvm"; ls /sys/kernel/debug/kvm 2>/dev/null | head; else echo "no (mount -t debugfs none /sys/kernel/debug, or counters unavailable)"; fi'
run "perf available?" \
    sh -c 'command -v perf >/dev/null && perf --version || echo "(perf not installed — VM-exit reason histogram will be skipped; debugfs counts still work)"'

# --- 5. Guest-side (L2) companion commands ----------------------------------
emit ""
emit "############################################################"
emit "# Run these INSIDE a sandbox (L2) for the matching guest view:"
emit "#   systemd-detect-virt              # expect: kvm  (Firecracker presents as kvm)"
emit "#   dmesg | grep -i kvm-clock        # confirm paravirt clock"
emit "#   cat /sys/devices/system/clocksource/clocksource0/current_clocksource"
emit "#   grep -c ^processor /proc/cpuinfo # vCPU count (expect 2)"
emit "############################################################"

[ -n "$OUT" ] && echo "(saved to $OUT)"
