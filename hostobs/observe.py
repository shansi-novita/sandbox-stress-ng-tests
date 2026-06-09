#!/usr/bin/env python3
"""observe.py — L1-side host observer for nested-virtualization analysis.

Runs as root on the L1 host (the VM where the orchestrator + Firecracker run),
CONCURRENTLY with a `BACKEND=e2b` perf batch. It watches Firecracker processes;
each fresh sandbox = one Firecracker process = one perf case (cases run serially,
one sandbox at a time). For each, over the process's whole lifetime, it records:

  * VM-exit counters         — per-VM KVM debugfs deltas. On standard Ubuntu the
                               kernel creates /sys/kernel/debug/kvm/<pid>-<fd>/ per
                               running VM, so each Firecracker process is attributed
                               exactly. (A global-aggregate read is kept only as a
                               labeled degraded fallback if no per-VM dir is found.)
  * VM-exit reason histogram — optional, `perf kvm stat` (set HOSTOBS_PERF=1; adds
                               tracepoint overhead on L1, never touches L2)
  * Firecracker host CPU     — /proc/<pid>/stat utime+stime, split into vCPU threads
                               (comm "fc_vcpu*") vs the rest (VMM/API/uffd)
  * ctxt switches            — /proc/<pid>/status vol/nonvol delta
  * L1 steal                 — /proc/stat steal delta over the window (is L0 also
                               preempting L1? that stacks on the nested cost)
  * cgroup                   — /sys/fs/cgroup/.../<sbx>/cpu.stat (throttling, usage)

Correlation key is the SANDBOX ID, parsed from the Firecracker --api-sock path
(fc-<sandboxID>-<rnd>.sock; see packages/shared/pkg/storage/sandbox.go). The
runner writes the same id into each result header as `# sandbox`, so analyze.py
joins host samples to cases exactly, not by guessing order.

Output: one JSON per sandbox at  <out_dir>/<sandboxID>.json  (default out_dir is
$HOSTOBS_DIR or ./hostobs-out/<timestamp>). Everything is read-only sampling.

Usage:
  sudo HOSTOBS_PERF=1 python3 observe.py [--out DIR] [--poll 0.5]
  (start it BEFORE the batch; Ctrl-C to stop after the batch finishes)
"""

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

# Curated subset used ONLY for the global-aggregate fallback (no per-VM dir). The
# normal per-VM path captures every single-int counter, so it needs no list. Names
# differ across arches (Intel vs AMD); missing ones are simply skipped.
KVM_COUNTERS = [
    "exits", "halt_exits", "mmio_exits", "io_exits", "signal_exits",
    "irq_exits", "irq_window_exits", "nmi_window_exits", "request_irq_exits",
    "insn_emulation", "insn_emulation_fail", "host_state_reload",
    "fpu_reload", "halt_wakeup", "halt_successful_poll", "l1d_flush",
    "tlb_flush", "hypercalls", "pf_fixed", "pf_guest", "invlpg", "nmi_injections",
]
KVM_DEBUGFS = "/sys/kernel/debug/kvm"
SOCK_RE = re.compile(r"fc-([a-z0-9]+)-[a-z0-9]+\.sock")
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# --- low-level readers (all read-once, never block) -------------------------
def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().split()[0])
    except Exception:
        return None


def l1_steal_total():
    """(steal_jiffies, total_jiffies) from the aggregate cpu line of /proc/stat."""
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu "):
                    v = [int(x) for x in line.split()[1:9]]  # ...idle iowait irq softirq steal
                    return v[7], sum(v)
    except Exception:
        pass
    return None, None


def proc_cpu_jiffies(pid):
    """Whole-process utime+stime (jiffies) from /proc/<pid>/stat (fields 14,15)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        # comm may contain spaces/parens; split after the last ')'.
        rest = data[data.rfind(")") + 1:].split()
        # rest[0]=state(field3); utime=field14 -> rest[11], stime=field15 -> rest[12]
        return int(rest[11]) + int(rest[12])
    except Exception:
        return None


def proc_ctxt(pid):
    vol = nonvol = None
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("voluntary_ctxt_switches"):
                    vol = int(line.split()[1])
                elif line.startswith("nonvoluntary_ctxt_switches"):
                    nonvol = int(line.split()[1])
    except Exception:
        pass
    return vol, nonvol


def vcpu_thread_cpu(pid):
    """Sum host CPU jiffies of vCPU threads (comm starts 'fc_vcpu') vs the rest."""
    vcpu = other = 0
    found = False
    for tdir in glob.glob(f"/proc/{pid}/task/*"):
        try:
            with open(f"{tdir}/comm") as f:
                comm = f.read().strip()
            with open(f"{tdir}/stat") as f:
                rest = f.read().rsplit(")", 1)[1].split()
            j = int(rest[11]) + int(rest[12])
        except Exception:
            continue
        found = True
        if comm.startswith("fc_vcpu"):
            vcpu += j
        else:
            other += j
    return (vcpu, other) if found else (None, None)


def _read_single_int(path):
    """Value of a debugfs counter, but ONLY if it is a lone integer (skips the
    *_hist histogram files, which hold many numbers)."""
    try:
        with open(path) as f:
            toks = f.read().split()
        if len(toks) == 1:
            return int(toks[0])
    except Exception:
        pass
    return None


def kvm_vm_dir(pid):
    """Per-VM debugfs dir for this process, e.g. /sys/kernel/debug/kvm/<id>-<fd>.

    The dir is named by the task that called KVM_CREATE_VM — usually the main pid,
    but it can be any thread of the Firecracker process. Match on the pid OR any of
    its tids so we never wrongly fall back to the (multi-VM) global aggregate.
    """
    ids = {pid}
    try:
        ids |= {int(os.path.basename(t)) for t in glob.glob(f"/proc/{pid}/task/*")}
    except Exception:
        pass
    for d in glob.glob(f"{KVM_DEBUGFS}/*-*"):
        base = os.path.basename(d).split("-")[0]
        if base.isdigit() and int(base) in ids and os.path.isdir(d):
            return d
    return None


def kvm_counters(vm_dir):
    """Snapshot KVM exit counters.

    From a per-VM dir (standard Ubuntu) capture EVERY single-integer counter, so the
    set is arch-agnostic (AMD/SVM and Intel/VMX expose different names — e.g. AMD has
    no nmi_window_exits). Only as a labeled degraded fallback (no per-VM dir found)
    read the curated subset from the global aggregate.
    """
    base = vm_dir or KVM_DEBUGFS
    if not os.path.isdir(base):
        return None
    snap = {}
    if vm_dir:
        for name in os.listdir(base):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                v = _read_single_int(p)
                if v is not None:
                    snap[name] = v
    else:
        for c in KVM_COUNTERS:
            v = _read_single_int(os.path.join(base, c))
            if v is not None:
                snap[c] = v
    return snap or None


def cgroup_cpu_stat(sandbox_id):
    """Find a cgroup v2 cpu.stat whose path mentions this sandbox id; return dict."""
    for path in glob.glob(f"/sys/fs/cgroup/**/*{sandbox_id}*/cpu.stat", recursive=True):
        try:
            out = {}
            with open(path) as f:
                for line in f:
                    k, _, v = line.partition(" ")
                    out[k] = int(v)
            out["_path"] = path
            return out
        except Exception:
            continue
    return None


def list_fc():
    """Map sandbox_id -> pid for currently running Firecracker processes."""
    out = {}
    try:
        res = subprocess.run(["pgrep", "-af", "firecracker"],
                             capture_output=True, text=True)
    except Exception:
        return out
    for line in res.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, cmdline = parts
        m = SOCK_RE.search(cmdline)
        if not m:
            continue
        try:
            out[m.group(1)] = int(pid)
        except ValueError:
            continue
    return out


# --- per-sandbox capture ----------------------------------------------------
class Capture:
    """Start/end snapshots for one Firecracker process, plus optional perf."""

    def __init__(self, sandbox_id, pid, use_perf, out_dir):
        self.sid = sandbox_id
        self.pid = pid
        self.out_dir = out_dir
        self.start_iso = now_iso()
        self.start_mono = time.monotonic()
        self.vm_dir = kvm_vm_dir(pid)
        self.kvm_source = ("per-vm:" + self.vm_dir) if self.vm_dir else "global-aggregate"
        self.kvm0 = kvm_counters(self.vm_dir)
        self.cpu0 = proc_cpu_jiffies(pid)
        self.vol0, self.nonvol0 = proc_ctxt(pid)
        self.steal0, self.total0 = l1_steal_total()
        self.perf_proc = None
        self.perf_data = None
        if use_perf:
            self._start_perf()

    def _start_perf(self):
        self.perf_data = os.path.join(self.out_dir, f".perf-{self.sid}.data")
        try:
            # perf kvm stat record attached to the FC pid; it exits when the pid
            # does. stderr/stdout silenced; report is parsed at finalize().
            self.perf_proc = subprocess.Popen(
                ["perf", "kvm", "stat", "record", "-p", str(self.pid),
                 "-o", self.perf_data],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self.perf_proc = None

    def _stop_perf(self):
        if not self.perf_proc:
            return None
        try:
            if self.perf_proc.poll() is None:
                self.perf_proc.send_signal(signal.SIGINT)
                try:
                    self.perf_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.perf_proc.kill()
        except Exception:
            pass
        # Parse the reason histogram from `perf kvm stat report`.
        try:
            rep = subprocess.run(["perf", "kvm", "stat", "report", "-i", self.perf_data],
                                 capture_output=True, text=True, timeout=60)
            reasons = self._parse_perf_report(rep.stdout)
            return {"raw": rep.stdout, "reasons": reasons}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        finally:
            try:
                os.remove(self.perf_data)
            except Exception:
                pass

    @staticmethod
    def _parse_perf_report(text):
        """Pull (reason, count, %time) rows out of perf kvm stat report output."""
        reasons = []
        for line in text.splitlines():
            # rows look like:  EXTERNAL_INTERRUPT  12345  56.78%  ...
            m = re.match(r"\s*([A-Z_]{3,})\s+(\d+)\s+([\d.]+)%", line)
            if m:
                reasons.append({"reason": m.group(1), "count": int(m.group(2)),
                                "pct_time": float(m.group(3))})
        return reasons

    def finalize(self):
        end_iso = now_iso()
        dur = round(time.monotonic() - self.start_mono, 2)
        kvm1 = kvm_counters(self.vm_dir or kvm_vm_dir(self.pid))
        cpu1 = proc_cpu_jiffies(self.pid)
        vol1, nonvol1 = proc_ctxt(self.pid)
        steal1, total1 = l1_steal_total()
        vcpu, other = vcpu_thread_cpu(self.pid)  # best-effort; may be gone already

        def delta(a, b):
            return (b - a) if (a is not None and b is not None) else None

        kvm_delta = None
        if self.kvm0 and kvm1:
            kvm_delta = {k: kvm1[k] - self.kvm0[k]
                         for k in self.kvm0 if k in kvm1}

        steal_pct = None
        if None not in (self.steal0, steal1, self.total0, total1) and total1 > self.total0:
            steal_pct = round(100.0 * (steal1 - self.steal0) / (total1 - self.total0), 3)

        fc_cpu_jiff = delta(self.cpu0, cpu1)
        rec = {
            "sandbox_id": self.sid,
            "pid": self.pid,
            "started": self.start_iso,
            "finished": end_iso,
            "lifetime_s": dur,
            "kvm_source": self.kvm_source,
            "vm_exits": kvm_delta,
            "fc_host_cpu_s": round(fc_cpu_jiff / CLK_TCK, 3) if fc_cpu_jiff is not None else None,
            "fc_vcpu_cpu_s": round(vcpu / CLK_TCK, 3) if vcpu is not None else None,
            "fc_other_cpu_s": round(other / CLK_TCK, 3) if other is not None else None,
            "fc_ctxt_voluntary": delta(self.vol0, vol1),
            "fc_ctxt_nonvoluntary": delta(self.nonvol0, nonvol1),
            "l1_steal_pct": steal_pct,
            "cgroup_cpu_stat": cgroup_cpu_stat(self.sid),
        }
        perf = self._stop_perf()
        if perf is not None:
            rec["perf_kvm"] = perf
        return rec


def main():
    ap = argparse.ArgumentParser(description="L1-side Firecracker/VM-exit observer")
    ap.add_argument("--out", default=os.environ.get("HOSTOBS_DIR"),
                    help="output dir (default $HOSTOBS_DIR or ./hostobs-out/<ts>)")
    ap.add_argument("--poll", type=float, default=float(os.environ.get("HOSTOBS_POLL", "0.5")),
                    help="poll interval seconds (default 0.5)")
    args = ap.parse_args()

    use_perf = os.environ.get("HOSTOBS_PERF", "0") == "1"
    out_dir = args.out or os.path.join("hostobs-out", datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)

    if os.geteuid() != 0:
        print("WARNING: not root — /proc/<pid>/stat of other procs, KVM debugfs and "
              "perf may be inaccessible.", file=sys.stderr)
    if not os.path.isdir(KVM_DEBUGFS):
        print(f"WARNING: {KVM_DEBUGFS} absent — VM-exit counts unavailable "
              "(try: mount -t debugfs none /sys/kernel/debug).", file=sys.stderr)
    if use_perf and not _has_perf():
        print("WARNING: HOSTOBS_PERF=1 but `perf` not usable — reason histogram skipped.",
              file=sys.stderr)
        use_perf = False

    # Firecracker processes already running when we start = other (pre-existing)
    # sandboxes; ignore them so we only attribute the test batch's sandboxes.
    baseline = set(list_fc().keys())
    print(f"[observe] out={out_dir} poll={args.poll}s perf={use_perf} "
          f"ignoring {len(baseline)} pre-existing sandbox(es). Ctrl-C to stop.",
          flush=True)

    active = {}   # sandbox_id -> Capture
    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))

    try:
        while not stop["v"]:
            current = list_fc()
            # New sandboxes (not pre-existing, not already tracked) -> start capture.
            for sid, pid in current.items():
                if sid in baseline or sid in active:
                    continue
                active[sid] = Capture(sid, pid, use_perf, out_dir)
                print(f"[observe] + {sid} pid={pid} "
                      f"({active[sid].kvm_source.split(':')[0]})", flush=True)
            # Disappeared sandboxes -> finalize + write JSON.
            for sid in [s for s in active if s not in current]:
                rec = active.pop(sid).finalize()
                _write(out_dir, sid, rec)
                ex = rec.get("vm_exits", {})
                n = ex.get("exits") if ex else None
                print(f"[observe] - {sid} exits={n} fc_cpu={rec.get('fc_host_cpu_s')}s "
                      f"l1_steal={rec.get('l1_steal_pct')}%", flush=True)
            time.sleep(args.poll)
    finally:
        # Flush any still-running captures on shutdown.
        for sid, cap in active.items():
            _write(out_dir, sid, cap.finalize())
        print(f"[observe] stopped; wrote captures to {out_dir}", flush=True)


def _write(out_dir, sid, rec):
    with open(os.path.join(out_dir, f"{sid}.json"), "w") as f:
        json.dump(rec, f, indent=2)


def _has_perf():
    try:
        subprocess.run(["perf", "--version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
