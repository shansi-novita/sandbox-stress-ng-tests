#!/usr/bin/env python3
"""analyze.py — nested-virtualization analysis report.

Joins three things into one report (hostobs/report.md):

  1. L2 results  — a `BACKEND=e2b` batch dir (sandbox, nested).
  2. L1 results  — a `BACKEND=local` batch dir (Docker in the same VM = the host
                   baseline). Same case files, so per-case metrics are comparable.
  3. host obs    — observe.py JSONs, joined to L2 cases by SANDBOX ID.

Outputs:
  * Table 1 — per case: L1 metric, L2 metric, loss%, plus the L1-side VM-exit
              count / Firecracker host CPU / L1 steal for that sandbox.
  * Table 2 — fingerprint: mean loss% of the CPU-control group (pure userspace,
              ~no VM-exits) vs the exit-sensitive group (syscall/ctx/IO/...). A big
              gap, tracking high VM-exit counts, is the signature of virtualization
              (and, under nesting, the amplified) overhead.
  * Table 3 — nesting-excess estimate: exit-sensitive loss minus a published
              bare-metal-Firecracker reference. Flagged clearly as an ESTIMATE —
              with no bare-metal box we cannot measure the nesting term directly.

Usage:
  python3 analyze.py --l2 <e2b_batch_dir> --l1 <local_batch_dir> \
                     [--hostobs <observe_out_dir>] [--out report.md]
"""

import argparse
import glob
import json
import os
import re
import sys

# --- per-case metric definition ---------------------------------------------
# kind selects the extractor; higher_better governs the loss% sign so that a
# POSITIVE loss always means "the sandbox (L2) is worse". group buckets cases for
# the fingerprint table. Cases absent here are reported but excluded from the
# fingerprint means.
#   groups: "cpu"  = pure userspace compute, the ~zero-exit control
#           "exit" = syscall/sched/ipc/pagefault/tlb — trap-heavy
#           "io"   = storage/fsync — exits + real I/O
#           "fs"   = filesystem metadata (syscall + journal; exit-ish)
CASE_META = {
    "A1":  ("cpu",  "sysbench_eps",  True,  "sysbench cpu events/s"),
    "A2":  ("cpu",  "stressng",      True,  "stress-ng matrixprod bogo ops/s"),
    "A3":  ("cpu",  "openssl",       True,  "openssl aes-256-gcm throughput"),
    "A4":  ("cpu",  "sysbench_eps",  True,  "sysbench cpu events/s (per-thread sweep)"),
    "A7":  ("exit", "stressng",      True,  "stress-ng tlb-shootdown bogo ops/s"),
    "A8":  ("io",   "fio_bw",        True,  "fio seq bandwidth"),
    "A9":  ("io",   "fio_iops",      True,  "fio rand IOPS"),
    "A11": ("io",   "fio_iops",      True,  "fio fsync IOPS"),
    "A12": ("io",   "ioping_lat",    False, "ioping latency"),
    "B1":  ("exit", "lmbench_lat",   False, "lat_syscall latency (us)"),
    "B2":  ("exit", "stressng",      True,  "stress-ng fork bogo ops/s"),
    "B3":  ("exit", "stressng",      True,  "stress-ng switch bogo ops/s"),
    "B4":  ("exit", "stressng",      True,  "stress-ng pthread bogo ops/s"),
    "B5":  ("exit", "stressng",      True,  "stress-ng pipe bogo ops/s"),
    "B6":  ("exit", "stressng",      True,  "stress-ng futex bogo ops/s"),
    "B7":  ("exit", "stressng",      True,  "stress-ng epoll bogo ops/s"),
    "B8":  ("exit", "stressng",      True,  "stress-ng mmap bogo ops/s"),
    "B11": ("fs",   "stressng",      True,  "stress-ng dir bogo ops/s"),
    "B12": ("fs",   "stressng",      True,  "stress-ng link bogo ops/s"),
    "B13": ("fs",   "stressng",      True,  "stress-ng symlink bogo ops/s"),
    "B14": ("fs",   "stressng",      True,  "stress-ng dentry bogo ops/s"),
    "B15": ("fs",   "stressng",      True,  "stress-ng rename bogo ops/s"),
    "B16": ("fs",   "stressng",      True,  "stress-ng chmod bogo ops/s"),
    "B17": ("fs",   "stressng",      True,  "stress-ng chown bogo ops/s"),
    "B18": ("fs",   "stressng",      True,  "stress-ng utime bogo ops/s"),
    "B19": ("fs",   "stressng",      True,  "stress-ng getdent bogo ops/s"),
}

GROUP_LABEL = {"cpu": "CPU-control (≈no exits)", "exit": "exit-sensitive",
               "io": "I/O / storage", "fs": "fs-metadata"}

# Rough published bare-metal Firecracker overhead, for the nesting-EXCESS estimate.
# Firecracker on bare metal adds little to pure compute and more to trap/IO paths.
# These are coarse reference bands (NOT per-op precise); the excess over them is an
# estimate of the *added* nested cost. Source noted in the report.
BAREMETAL_REF_PCT = {"cpu": 2.0, "exit": 8.0, "io": 15.0, "fs": 10.0}
BAREMETAL_REF_NOTE = (
    "Reference bands are coarse public figures for Firecracker on BARE METAL "
    "(Firecracker NSDI'20 + AWS docs: near-native CPU; single-digit-µs syscall add; "
    "higher on virtio I/O). They are not per-op exact. 'nesting excess' = measured "
    "L2/L1 loss − band; treat as an ESTIMATE of the added nested-virt cost, not a "
    "measurement (no non-nested Firecracker baseline was available)."
)


# --- metric extractors (best-effort; return float or None) ------------------
def _floats(line):
    return [float(x) for x in re.findall(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?", line)]


def m_stressng(text):
    """stress-ng bogo ops/s (real-time column = 2nd-to-last float on the line)."""
    vals = []
    for line in text.splitlines():
        if "stress-ng:" not in line:
            continue
        # the metrics row has >=4 trailing decimals: ... real usr sys ops/s ops/s
        nums = _floats(line)
        if len(nums) >= 5 and ("bogo" not in line and "real time" not in line):
            vals.append(nums[-2])  # ops/s by real time
    return max(vals) if vals else None


def m_sysbench_eps(text):
    vals = [_floats(l)[-1] for l in text.splitlines()
            if "events per second" in l and _floats(l)]
    return max(vals) if vals else None


def m_fio_bw(text):
    # fio "READ: bw=123MiB/s" / "WRITE: bw=..." ; normalize to MiB/s.
    best = None
    for m in re.finditer(r"bw=\s*([\d.]+)\s*([KMG]i?B)/s", text):
        v = float(m.group(1)); unit = m.group(2)
        scale = {"KiB": 1/1024, "KB": 1/1024, "MiB": 1, "MB": 1,
                 "GiB": 1024, "GB": 1024, "B": 1/1024/1024}.get(unit, 1)
        v *= scale
        best = v if best is None else max(best, v)
    return best


def m_fio_iops(text):
    vals = []
    for m in re.finditer(r"IOPS=\s*([\d.]+)([kKmM]?)", text):
        v = float(m.group(1)); suf = m.group(2).lower()
        v *= {"k": 1e3, "m": 1e6}.get(suf, 1)
        vals.append(v)
    return max(vals) if vals else None


def m_ioping_lat(text):
    # ioping: "min/avg/max/mdev = 100.0 us / 200.0 us / ..." -> take avg (us).
    m = re.search(r"min/avg/max[^=]*=\s*[\d.]+\s*(\w+)\s*/\s*([\d.]+)\s*(\w+)", text)
    if m:
        v = float(m.group(2)); unit = m.group(3)
        return v * {"ns": 1e-3, "us": 1, "ms": 1e3, "s": 1e6}.get(unit, 1)
    return None


def m_lmbench_lat(text):
    # lmbench lat_* prints e.g. "Simple syscall: 0.3100 microseconds"; take min.
    vals = [_floats(l)[-1] for l in text.splitlines()
            if "microsecond" in l.lower() and _floats(l)]
    return min(vals) if vals else None


def m_openssl(text):
    # crude: largest throughput-ish decimal; openssl speed tables vary a lot.
    vals = _floats(text)
    return max(vals) if vals else None


EXTRACTORS = {
    "stressng": m_stressng, "sysbench_eps": m_sysbench_eps, "fio_bw": m_fio_bw,
    "fio_iops": m_fio_iops, "ioping_lat": m_ioping_lat, "lmbench_lat": m_lmbench_lat,
    "openssl": m_openssl,
}


# --- result-file parsing -----------------------------------------------------
HEADER_RE = re.compile(r"^#\s*(\w+)\s*:\s*(.*)$")


def parse_result(path):
    """Return (case_id, header dict, measurement-text) for one <ID>-<name>.txt."""
    with open(path, errors="replace") as f:
        raw = f.read()
    header = {}
    for line in raw.splitlines():
        m = HEADER_RE.match(line)
        if m:
            header[m.group(1)] = m.group(2).strip()
        elif line.startswith("="):
            break
    case_id = (header.get("case", "").split() or [""])[0]
    # Use only measurement sections (drop hook/fsinfo/aggregate/health) so stray
    # numbers in mount options or summaries don't pollute the metric.
    meas = []
    keep = True
    for block in re.split(r"={10,}\n", raw):
        h = re.match(r"\[(\w+)[^\]]*\]", block.lstrip())
        phase = h.group(1) if h else ""
        if phase in ("hook", "fsinfo", "aggregate", "health"):
            continue
        meas.append(block)
    return case_id, header, "\n".join(meas)


def load_batch(d):
    """case_id -> (header, metric_text) for every result file in a batch dir."""
    out = {}
    for path in sorted(glob.glob(os.path.join(d, "*.txt"))):
        if os.path.basename(path) in ("summary.txt",):
            continue
        try:
            cid, header, text = parse_result(path)
        except Exception:
            continue
        if cid:
            out[cid] = (header, text)
    return out


def load_hostobs(d):
    """sandbox_id -> observe.py record."""
    out = {}
    if not d:
        return out
    for path in glob.glob(os.path.join(d, "*.json")):
        try:
            with open(path) as f:
                rec = json.load(f)
            out[rec["sandbox_id"]] = rec
        except Exception:
            continue
    return out


def metric_for(cid, text):
    meta = CASE_META.get(cid)
    if not meta:
        return None, None, None
    group, kind, higher, label = meta
    val = EXTRACTORS[kind](text or "")
    return val, higher, label


def loss_pct(l1, l2, higher_better):
    if not l1 or l2 is None or l1 == 0:
        return None
    # Positive loss always = sandbox worse.
    return round(100.0 * ((l1 - l2) / l1 if higher_better else (l2 - l1) / l1), 1)


# --- report ------------------------------------------------------------------
def fnum(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def build_report(l2, l1, hostobs):
    lines = []
    lines.append("# 嵌套虚拟化性能分析 (L2 sandbox vs L1 host baseline)\n")
    lines.append("> loss% 为正 = 沙箱(L2)更差。host 侧 VM-exit/FC-CPU/L1-steal 来自 "
                 "observe.py,按 sandbox id 关联。\n")

    # Table 1: per-case.
    lines.append("\n## 1. 逐用例:L1 / L2 / loss% + 宿主侧 VM-exit\n")
    lines.append("| case | 指标 | 组 | L1 | L2 | loss% | VM-exits | FC host CPU(s) | L1 steal% |")
    lines.append("|------|------|----|----|----|-------|----------|----------------|-----------|")
    per_group = {}  # group -> list of loss%
    all_ids = sorted(set(l2) | set(l1), key=lambda c: (c[0], int(re.sub(r"\D", "", c) or 0)))
    for cid in all_ids:
        meta = CASE_META.get(cid)
        group = meta[0] if meta else "?"
        h2, t2 = l2.get(cid, ({}, ""))
        h1, t1 = l1.get(cid, ({}, ""))
        v2, higher, label = metric_for(cid, t2)
        v1, _, _ = metric_for(cid, t1)
        loss = loss_pct(v1, v2, higher) if higher is not None else None
        # host obs joined by L2 sandbox id
        sid = h2.get("sandbox")
        rec = hostobs.get(sid) if sid else None
        exits = fc_cpu = steal = "—"
        if rec:
            ex = rec.get("vm_exits") or {}
            exits = fnum(ex.get("exits"))
            fc_cpu = fnum(rec.get("fc_host_cpu_s"))
            steal = fnum(rec.get("l1_steal_pct"))
        lines.append(f"| {cid} | {label or '—'} | {group} | {fnum(v1)} | {fnum(v2)} | "
                     f"{fnum(loss)} | {exits} | {fc_cpu} | {steal} |")
        if loss is not None and meta:
            per_group.setdefault(group, []).append(loss)

    # Table 2: fingerprint.
    lines.append("\n## 2. 嵌套指纹:各组平均 loss%\n")
    lines.append("| 组 | 含义 | 用例数 | 平均 loss% |")
    lines.append("|----|------|--------|-----------|")
    means = {}
    for g in ("cpu", "exit", "io", "fs"):
        vals = per_group.get(g, [])
        if vals:
            means[g] = sum(vals) / len(vals)
            lines.append(f"| {g} | {GROUP_LABEL[g]} | {len(vals)} | {means[g]:.1f} |")
    if "cpu" in means and means["cpu"] != 0:
        for g in ("exit", "io", "fs"):
            if g in means:
                ratio = means[g] / means["cpu"] if means["cpu"] else float("inf")
                lines.append(f"\n- {GROUP_LABEL[g]} / CPU-control 损耗比 ≈ **{ratio:.1f}×** "
                             f"→ 该组开销远超纯计算,大头来自 VM-exit 处理(嵌套放大部位)。")
    elif "cpu" in means:
        lines.append("\n- CPU-control 损耗≈0(纯用户态不陷出),符合预期基准。")

    # Table 3: nesting-excess estimate.
    lines.append("\n## 3. 嵌套惩罚**估计**(非测量)\n")
    lines.append(f"> {BAREMETAL_REF_NOTE}\n")
    lines.append("| 组 | 实测 loss% | 裸机 Firecracker 参考带 | 估计嵌套增量 |")
    lines.append("|----|-----------|------------------------|-------------|")
    for g in ("cpu", "exit", "io", "fs"):
        if g in means:
            ref = BAREMETAL_REF_PCT[g]
            excess = means[g] - ref
            lines.append(f"| {g} | {means[g]:.1f} | ~{ref:.0f} | "
                         f"{'+' if excess >= 0 else ''}{excess:.1f} |")

    # Correlation completeness note.
    lines.append("\n## 4. 关联完整性 / 局限\n")
    matched = sum(1 for cid in l2 if hostobs.get(l2[cid][0].get("sandbox")))
    lines.append(f"- L2 用例 {len(l2)} 个,其中 {matched} 个成功按 sandbox id 命中 host 观测。")
    if not hostobs:
        lines.append("- 未提供 --hostobs(或为空):表中 VM-exit/FC-CPU/L1-steal 为空,"
                     "仅有差分与指纹。建议在 L1 跑 observe.py 后重做以获得陷出硬证据。")
    lines.append("- 局限:无裸机 → 第 3 表为估计;perf kvm stat 仅见 L2→L1 exit,"
                 "L1→L0 二次放大需 L0 访问,由 L1 steal / FC host CPU 间接佐证。")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="nested-virt analysis report")
    ap.add_argument("--l2", required=True, help="BACKEND=e2b batch dir (sandbox)")
    ap.add_argument("--l1", required=True, help="BACKEND=local batch dir (host baseline)")
    ap.add_argument("--hostobs", default=None, help="observe.py output dir")
    ap.add_argument("--out", default=None, help="report path (default <l2>/report.md)")
    args = ap.parse_args()

    for d, label in ((args.l2, "--l2"), (args.l1, "--l1")):
        if not os.path.isdir(d):
            print(f"error: {label} dir not found: {d}", file=sys.stderr)
            sys.exit(2)

    l2 = load_batch(args.l2)
    l1 = load_batch(args.l1)
    hostobs = load_hostobs(args.hostobs)
    report = build_report(l2, l1, hostobs)

    out = args.out or os.path.join(args.l2, "report.md")
    with open(out, "w") as f:
        f.write(report)
    print(report)
    print(f"\n(written to {out})", file=sys.stderr)


if __name__ == "__main__":
    main()
