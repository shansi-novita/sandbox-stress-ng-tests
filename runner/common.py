"""Shared harness for the E2B sandbox performance test framework.

Each test case is one python file under cases/ that calls run_case(). Every case
runs in its own fresh sandbox (isolation). Memory cases run cold -> warmup -> warm
in the SAME sandbox to expose the userfaultfd (uffd) cold-start cost.

Config via environment variables:
  E2B_DOMAIN        self-hosted deployment domain (omit for e2b.dev)
  E2B_API_KEY       read automatically by the SDK
  PERF_TEMPLATE     template name/id to launch (default: perf-bench)
  DURATION          seconds per stress-ng/fio time-based test (default: 30)
  WARMUP_FRACTION   fraction of guest RAM to pre-fault during warmup (default: 0.85)
  STORAGE_DIR       where storage tests write inside the sandbox (default: /perf-io,
                    a dir on the root filesystem; storage cases run as root to create it)
  IPERF_SERVER / NETPERF_SERVER / WRK_TARGET   enable the network cases
  BATCH_DIR         results dir (set by run_all.py; auto-created when run standalone)
  RESULTS_ROOT      base dir for auto-created batch dirs (default: runner/results)
  STREAM            1 (default) streams live command output to the terminal; 0 to silence
  BACKEND           e2b (default) runs each case in a sandbox; local runs commands
                    directly via subprocess (host baseline inside a Docker container)
"""

import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

# --- config -----------------------------------------------------------------
BACKEND = os.environ.get("BACKEND", "e2b")  # "e2b" | "local"
DOMAIN = os.environ.get("E2B_DOMAIN") or None
TEMPLATE = os.environ.get("PERF_TEMPLATE", "perf-bench")
DURATION = int(os.environ.get("DURATION", "30"))
WARMUP_FRACTION = float(os.environ.get("WARMUP_FRACTION", "0.85"))
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/perf-io")
STREAM = os.environ.get("STREAM", "1") != "0"  # live-stream output to terminal
# Single-worker cases pin their shell (and all children) to this core so they never
# migrate across the sandbox's vCPUs — a clean, reproducible single-core baseline.
# Set PIN_CORE="" to disable globally; individual cases opt out with run_case(pin=False).
PIN_CORE = os.environ.get("PIN_CORE", "1")
# Optional shell command run ONCE as root in each fresh sandbox (or the local
# container) BEFORE the case commands — for tuning the environment, e.g.
#   SANDBOX_HOOK='mount -o remount,noatime /'
#   SANDBOX_HOOK='DEV=$(basename $(findmnt -no SOURCE /)); echo 64 > /sys/fs/ext4/$DEV/inode_readahead_blks'
# Recorded as a [hook] section; its exit code counts toward the case status so a
# failed tuning step is visible. Empty = disabled. NOT placeholder-substituted.
HOOK = os.environ.get("SANDBOX_HOOK", "").strip()

# --- perf tracing of stress-ng (opt-in, both backends) ----------------------
# When PERF_TRACE is on, every measured command matching PERF_MATCH is run UNDER
# perf instead of the /usr/bin/time health wrapper (in-place replacement), to get
# a perf stat counter summary and a perf report hotspot table into the result.
# Firecracker guests / most cloud VMs expose NO hardware PMU, so sampling defaults
# to the software event cpu-clock (works without a vPMU); hardware perf-stat events
# will show "<not supported>", which itself confirms the missing vPMU.
PERF_TRACE = os.environ.get("PERF_TRACE", "0") != "0"
PERF_MATCH = os.environ.get("PERF_MATCH", "stress-ng")  # regex; which cmds to trace
PERF_MODE = os.environ.get("PERF_MODE", "both")          # both | stat | record
PERF_EVENT = os.environ.get("PERF_EVENT", "cpu-clock")   # record sampling event
PERF_FREQ = os.environ.get("PERF_FREQ", "999")           # record sampling frequency
PERF_REPORT_LINES = os.environ.get("PERF_REPORT_LINES", "40")  # hotspot table cap

# e2b.dev rejects Sandbox.create timeouts greater than 1 hour; cap the computed
# sandbox lifetime so long cases (e.g. B10 UnixBench) still start instead of 400ing.
SANDBOX_MAX_TIMEOUT = 3600

# Read-only probe appended to every case as an [fsinfo] section: the mount options
# and (ext4) feature flags of / (the filesystem the metadata tests now write to).
# Lets dir_index / has_journal / noatime / journal-mode explain the numbers. Always
# exits 0 (trailing `true`) so it never affects the case status. Needs root for
# tune2fs to read the block device, so it is run as root (like the setup hook).
FSINFO_CMD = (
    'echo "# mount /"; '
    'findmnt -no SOURCE,FSTYPE,OPTIONS / 2>/dev/null || grep " / " /proc/mounts; '
    'D=$(findmnt -no SOURCE / 2>/dev/null); '
    'echo "# ext4 features (/ -> $D)"; '
    'tune2fs -l "$D" 2>/dev/null | grep -iE '
    '"Filesystem volume|Filesystem features|Default mount options|Block size|Inode size|Inode count" '
    '|| echo "(tune2fs unavailable / not ext4 / needs root)"; '
    'true'
)

# Placeholder values substituted into every command string via str.format().
SUBST = {
    "duration": DURATION,
    "storage_dir": STORAGE_DIR,
    "iperf_server": os.environ.get("IPERF_SERVER", ""),
    "netperf_server": os.environ.get("NETPERF_SERVER", ""),
    "wrk_target": os.environ.get("WRK_TARGET", ""),
}


def batch_dir():
    """Results directory for this run; created on first use."""
    d = os.environ.get("BATCH_DIR")
    if not d:
        root = os.environ.get("RESULTS_ROOT") or os.path.join(os.path.dirname(__file__), "results")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        d = os.path.join(root, stamp)
        os.environ["BATCH_DIR"] = d
    os.makedirs(d, exist_ok=True)
    return d


def warmup_command():
    """Pre-fault ~WARMUP_FRACTION of guest RAM to pay the uffd cost up front."""
    return (
        "python3 -c \"import os; "
        "p=os.sysconf('SC_PAGE_SIZE'); "
        f"n=int(os.sysconf('SC_PHYS_PAGES')*p*{WARMUP_FRACTION}); "
        "b=bytearray(n); "
        "[b.__setitem__(i,1) for i in range(0,n,p)]; "
        "print('warmed', n, 'bytes')\""
    )


def make_sandbox(timeout):
    from e2b import Sandbox  # imported lazily so BACKEND=local needs no e2b package
    kwargs = {"timeout": timeout}
    if DOMAIN:
        kwargs["domain"] = DOMAIN
    return Sandbox.create(TEMPLATE, **kwargs)


# --- per-run environment health (steal/iowait/ctxt + rusage) ----------------
# We capture a small set of metrics AROUND each measured command so each run can
# be judged for trustworthiness (e.g. a run robbed of CPU by host steal). All are
# read-once counters the kernel already maintains -> zero perturbation, unlike perf
# which hooks per-event tracepoints. The block is carried out-of-band on stderr.
_H_BEGIN = "<<<E2BPERFHEALTH"
_H_END = "E2BPERFHEALTH>>>"
_H_SEP = "--E2BSEP--"


def _measure_wrapper(cmd, pin_core):
    """Wrap cmd to capture per-run health without polluting its own output.

    The real command is written verbatim to a temp script via a quoted heredoc (so
    no re-quoting is needed), then run under /usr/bin/time -v (rusage -> file) with
    /proc/stat snapshotted right before and after. When pin_core is not None the
    wrapping shell pins itself with taskset and every child inherits that affinity
    (no CAP_SYS_NICE needed for self-pinning within the allowed set). The health
    block is appended to stderr between unique markers and sliced out by the caller,
    leaving the command's own stdout/stderr untouched.
    """
    pin = f"taskset -pc {pin_core} $$ >/dev/null 2>&1\n" if pin_core is not None else ""
    return (
        f"{pin}"
        "__B=/tmp/.e2bperf.$$\n"
        "cat > $__B.sh <<'__E2B_PERF_CMD_EOF__'\n"
        f"{cmd}\n"
        "__E2B_PERF_CMD_EOF__\n"
        "__T=$(command -v /usr/bin/time || true)\n"
        "cat /proc/stat > $__B.0 2>/dev/null\n"
        'if [ -n "$__T" ]; then "$__T" -v -o $__B.t bash $__B.sh; __RC=$?; '
        "else bash $__B.sh; __RC=$?; fi\n"
        "cat /proc/stat > $__B.1 2>/dev/null\n"
        f'( echo "{_H_BEGIN}"; cat $__B.0 2>/dev/null; echo "{_H_SEP}"; '
        f'cat $__B.1 2>/dev/null; echo "{_H_SEP}"; cat $__B.t 2>/dev/null; '
        f'echo "{_H_END}" ) >&2\n'
        "rm -f $__B.sh $__B.0 $__B.1 $__B.t 2>/dev/null\n"
        "exit $__RC\n"
    )


def perf_wrapper(cmd, pin_core):
    """Wrap cmd to run UNDER perf (stat + record→report) for hotspot/perf analysis.

    Replaces the health wrapper for stress-ng runs (PERF_TRACE). The command goes
    verbatim into a temp script via a quoted heredoc (no re-quoting). We best-effort
    relax perf_event_paranoid/kptr_restrict (needs root; harmless otherwise) so the
    kernel side — where the virtualization/trap cost lives — resolves to symbols.
    Sampling uses PERF_EVENT (default cpu-clock) so it works with no hardware PMU.
    The section exit code is the benchmark's own rc, not perf's, so case status
    still reflects whether stress-ng succeeded. Output (stat summary + hotspot top)
    goes to stdout between clear markers.
    """
    pin = f"taskset -c {pin_core} " if pin_core is not None else ""
    do_stat = PERF_MODE in ("both", "stat")
    do_record = PERF_MODE in ("both", "record")
    lines = []
    lines.append("echo -1 > /proc/sys/kernel/perf_event_paranoid 2>/dev/null || true")
    lines.append("echo 0 > /proc/sys/kernel/kptr_restrict 2>/dev/null || true")
    lines.append("__B=/tmp/.e2bperf.$$")
    lines.append("cat > $__B.sh <<'__E2B_PERF_CMD_EOF__'")
    lines.append(cmd)
    lines.append("__E2B_PERF_CMD_EOF__")
    lines.append("__P=$(command -v perf || echo perf)")
    lines.append("__RC=0")
    lines.append(f'echo "### perf-traced (mode={PERF_MODE} event={PERF_EVENT}) '
                 '— timing/health below is perturbed by perf sampling ###"')
    if do_stat:
        lines.append('echo "=== perf stat ==="')
        lines.append(f'{pin}"$__P" stat -o $__B.stat -- bash $__B.sh; __RC=$?')
        lines.append("cat $__B.stat 2>/dev/null")
    if do_record:
        lines.append('echo "=== perf report (hotspots) ==="')
        lines.append(
            f'{pin}"$__P" record -e {PERF_EVENT} -F {PERF_FREQ} -g -o $__B.data '
            "-- bash $__B.sh >/dev/null 2>&1; __RC2=$?")
        if not do_stat:  # record's rc is the only signal of the benchmark result
            lines.append("__RC=$__RC2")
        lines.append(
            f'"$__P" report --stdio --percent-limit 1 -i $__B.data 2>/dev/null '
            f"| head -n {PERF_REPORT_LINES} "
            '|| echo "(perf report unavailable — check perf permissions / event support)"')
    lines.append("rm -f $__B.sh $__B.stat $__B.data 2>/dev/null")
    lines.append("exit $__RC")
    return "\n".join(lines) + "\n"


def _last_int(line):
    m = re.findall(r"\d+", line)
    return int(m[-1]) if m else None


def _parse_health(block):
    """Two /proc/stat dumps + a time -v report -> dict of steal%/iowait%/ctxt/rusage."""
    parts = block.split(_H_SEP)

    def stat(s):
        steal = iowait = total = ctxt = None
        for line in s.splitlines():
            f = line.split()
            if not f:
                continue
            if f[0] == "cpu" and len(f) >= 9:
                v = [int(x) for x in f[1:9]]  # user nice system idle iowait irq softirq steal
                iowait, steal, total = v[4], v[7], sum(v)
            elif f[0] == "ctxt" and len(f) >= 2:
                ctxt = int(f[1])
        return steal, iowait, total, ctxt

    h = {}
    if len(parts) >= 2:
        s0, i0, t0, c0 = stat(parts[0])
        s1, i1, t1, c1 = stat(parts[1])
        if None not in (s0, s1, t0, t1) and t1 > t0:
            dt = t1 - t0
            h["steal_pct"] = round(100.0 * (s1 - s0) / dt, 3)
            h["iowait_pct"] = round(100.0 * (i1 - i0) / dt, 3)
        if None not in (c0, c1):
            h["ctxt"] = c1 - c0
    if len(parts) >= 3:
        for line in parts[2].splitlines():
            if "Involuntary context switches" in line:
                h["ivcsw"] = _last_int(line)
            elif "Voluntary context switches" in line:
                h["vcsw"] = _last_int(line)
            elif "Major" in line and "page faults" in line:
                h["majflt"] = _last_int(line)
            elif "Minor" in line and "page faults" in line:
                h["minflt"] = _last_int(line)
    return h or None


def _extract_health(text):
    """Slice the health block out of a captured stream; return (clean_text, health)."""
    i = text.find(_H_BEGIN)
    if i < 0:
        return text, None
    j = text.find(_H_END, i)
    if j < 0:
        return text, None
    health = _parse_health(text[i + len(_H_BEGIN):j])
    clean = text[:i] + text[j + len(_H_END):]
    return clean.strip("\n"), health


def _fmt_health(h):
    order = [("steal_pct", "steal", "%"), ("iowait_pct", "iowait", "%"),
             ("ctxt", "ctxt", ""), ("ivcsw", "ivcsw", ""), ("vcsw", "vcsw", ""),
             ("majflt", "majflt", ""), ("minflt", "minflt", "")]
    out = []
    for key, label, unit in order:
        if h.get(key) is not None:
            pre = "+" if key == "ctxt" else ""
            out.append(f"{label}={pre}{h[key]}{unit}")
    return " ".join(out)


def health_section(sections, steal_max=3.0):
    """Summarize per-run health so each run's trustworthiness can be judged.

    No hard gate / retry: we record the numbers and flag runs whose steal exceeds
    steal_max so contaminated measurements are obvious without discarding data.
    """
    rows = [s for s in sections if s.get("health")]
    if not rows:
        return []
    lines = ["per-run environment health (judge result trustworthiness):"]
    suspect = []
    for s in rows:
        h = s["health"]
        tag = ""
        st = h.get("steal_pct")
        if st is not None and st > steal_max:
            tag = f"  <-- SUSPECT (steal>{steal_max}%)"
            suspect.append(s["phase"])
        lines.append(f"  [{s['phase']}] {_fmt_health(h)}{tag}")
    if suspect:
        lines.append(f"verdict: {len(suspect)}/{len(rows)} run(s) likely contaminated "
                     f"by host CPU steal ({', '.join(suspect)}); treat as low-confidence.")
    else:
        lines.append(f"verdict: all {len(rows)} run(s) clean (steal<={steal_max}%).")
    return [{"phase": "health", "label": "environment health summary",
             "exit": 0, "elapsed": 0.0, "stdout": "\n".join(lines), "stderr": ""}]


def run_cmd(sbx, cmd, cmd_timeout, prefix="", user=None, do_format=True,
            pin_core=None, measure=False):
    """Run one command, returning a section dict with full raw output.

    When STREAM is on, output is echoed to the terminal live via SDK callbacks
    while also being buffered for the result file. user="root" runs it as root
    (needed by the setup hook: remount / sysfs writes). do_format=False skips
    placeholder substitution for literal shell (e.g. the hook's $DEV / ${VAR}).
    """
    if do_format:
        cmd = cmd.format(**SUBST)
    if STREAM:
        print(f"\n>>> {prefix} $ {cmd}", flush=True)
    # perf tracing replaces the health wrapper for matched (stress-ng) commands.
    use_perf = measure and PERF_TRACE and re.search(PERF_MATCH, cmd) is not None
    if use_perf:
        run = perf_wrapper(cmd, pin_core)
        if not user:  # perf needs root to bypass a strict perf_event_paranoid
            user = "root"
    elif measure:
        run = _measure_wrapper(cmd, pin_core)
    else:
        run = cmd
    out_buf, err_buf = [], []

    def on_out(d):
        out_buf.append(d)
        if STREAM:
            sys.stdout.write(d); sys.stdout.flush()

    # The health block is appended to stderr; stop echoing it live (it's verbose
    # raw /proc/stat + time -v) and print a single parsed line afterwards instead.
    suppress = {"on": False}

    def on_err(d):
        err_buf.append(d)
        if not STREAM or suppress["on"]:
            return
        combined = "".join(err_buf)
        idx = combined.find(_H_BEGIN)
        if idx < 0:
            sys.stderr.write(d); sys.stderr.flush()
        else:  # echo only the real stderr before the marker, then go quiet
            already = len(combined) - len(d)
            if idx > already:
                sys.stderr.write(combined[already:idx]); sys.stderr.flush()
            suppress["on"] = True

    start = time.time()
    try:
        kwargs = {"timeout": cmd_timeout, "on_stdout": on_out, "on_stderr": on_err}
        if user:
            kwargs["user"] = user
        res = sbx.commands.run(run, **kwargs)
        exit_code = res.exit_code
    except Exception as e:  # command error/timeout: capture what streamed, don't abort
        exit_code = -1
        msg = f"\n{type(e).__name__}: {e}"
        err_buf.append(msg)
        if STREAM:
            sys.stderr.write(msg); sys.stderr.flush()
    stderr_text = "".join(err_buf)
    health = None
    if measure and not use_perf:  # the health block rides on stderr; slice it back out
        stderr_text, health = _extract_health(stderr_text)
        if STREAM and health:
            print(f">>> {prefix} health: {_fmt_health(health)}", flush=True)
    section = {
        "label": cmd,
        "exit": exit_code,
        "elapsed": round(time.time() - start, 2),
        "stdout": "".join(out_buf),
        "stderr": stderr_text,
    }
    if health:
        section["health"] = health
    return section


def run_cmd_local(cmd, cmd_timeout, prefix="", do_format=True,
                  pin_core=None, measure=False):
    """Run one command locally via bash (BACKEND=local, inside the host container).

    stderr is merged into stdout so the live stream and captured buffer keep
    output ordering. A watchdog timer enforces cmd_timeout even if the command
    hangs while producing no output. Mirrors run_cmd's return shape. Because
    stderr is merged here, the measure health block arrives on stdout and is
    sliced out of that buffer.
    """
    if do_format:
        cmd = cmd.format(**SUBST)
    if STREAM:
        print(f"\n>>> {prefix} $ {cmd}", flush=True)
    # perf tracing replaces the health wrapper for matched (stress-ng) commands.
    # The container already runs as root; perf permissions come from run-host.sh
    # adding --cap-add SYS_ADMIN/PERFMON when PERF_TRACE is on.
    use_perf = measure and PERF_TRACE and re.search(PERF_MATCH, cmd) is not None
    if use_perf:
        run = perf_wrapper(cmd, pin_core)
    elif measure:
        run = _measure_wrapper(cmd, pin_core)
    else:
        run = cmd
    buf = []
    timed_out = {"v": False}
    start = time.time()
    try:
        p = subprocess.Popen(["bash", "-lc", run], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        timer = threading.Timer(cmd_timeout, lambda: (timed_out.__setitem__("v", True), p.kill()))
        timer.start()
        try:
            suppress = False  # stop echoing the verbose health block live
            for line in p.stdout:
                buf.append(line)
                if not suppress and _H_BEGIN in line:
                    suppress = True
                if STREAM and not suppress:
                    sys.stdout.write(line); sys.stdout.flush()
            p.wait()
        finally:
            timer.cancel()
        if timed_out["v"]:
            exit_code = -1
            msg = f"\nTimeoutExpired: exceeded {cmd_timeout}s"
            buf.append(msg)
            if STREAM:
                sys.stderr.write(msg); sys.stderr.flush()
        else:
            exit_code = p.returncode
    except Exception as e:  # spawn failure etc.: capture, don't abort the case
        exit_code = -1
        buf.append(f"\n{type(e).__name__}: {e}")
    stdout_text = "".join(buf)
    health = None
    if measure and not use_perf:  # stderr is merged into stdout here, so block is in stdout
        stdout_text, health = _extract_health(stdout_text)
        if STREAM and health:
            print(f">>> {prefix} health: {_fmt_health(health)}", flush=True)
    section = {
        "label": cmd,
        "exit": exit_code,
        "elapsed": round(time.time() - start, 2),
        "stdout": stdout_text,
        "stderr": "",
    }
    if health:
        section["health"] = health
    return section


def write_result(case_id, name, sandbox_id, sections, total_elapsed, status="ok",
                 started=None):
    path = os.path.join(batch_dir(), f"{case_id}-{name}.txt")
    with open(path, "w") as f:
        f.write(f"# case      : {case_id} {name}\n")
        f.write(f"# status    : {status}\n")
        f.write(f"# template  : {TEMPLATE}\n")
        f.write(f"# domain    : {DOMAIN or 'e2b.dev'}\n")
        f.write(f"# sandbox   : {sandbox_id}\n")
        f.write(f"# duration  : {DURATION}s/test\n")
        # started/finished bracket the measurement window so the L1-side host
        # observer (tests/perf/hostobs/observe.py) can cross-check, by time, the
        # Firecracker-process samples it correlated to this case by sandbox id.
        if started:
            f.write(f"# started   : {started}\n")
        f.write(f"# finished  : {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"# total_s   : {total_elapsed}\n")
        for s in sections:
            f.write("\n" + "=" * 70 + "\n")
            f.write(f"[{s.get('phase', 'run')}] $ {s['label']}\n")
            f.write(f"exit={s['exit']}  elapsed={s['elapsed']}s\n")
            if s.get("health"):
                f.write(f"health={_fmt_health(s['health'])}\n")
            f.write("-" * 70 + " stdout\n")
            f.write((s["stdout"] or "").rstrip() + "\n")
            if s["stderr"]:
                f.write("-" * 70 + " stderr\n")
                f.write(s["stderr"].rstrip() + "\n")
    return path


_DECIMAL_RE = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")


def _metrics(section):
    """Decimal scalar measurements extracted from a section's output.

    Requiring a decimal point excludes integer noise (thread counts, the digits
    inside an mktemp path, etc.) and keeps the real latency/throughput numbers.
    """
    text = (section.get("stdout") or "") + "\n" + (section.get("stderr") or "")
    return [float(x) for x in _DECIMAL_RE.findall(text)]


def aggregate_runs(commands, runs):
    """min/avg/max summary sections across repeated runs (one per command).

    When every repeat yields the same small set of scalar metrics, emit a
    positional min/avg/max table; otherwise note that the repeats were recorded
    as-is (the output isn't a fixed scalar set, e.g. a latency curve or a
    stress-ng metrics table) so variance can be eyeballed from the run sections.
    """
    out = []
    reps = len(runs)
    for i, cmd in enumerate(commands):
        per_run = [_metrics(runs[r][i]) for r in range(reps)]
        counts = {len(v) for v in per_run}
        if len(counts) == 1 and 1 <= next(iter(counts)) <= 16:
            m = next(iter(counts))
            lines = [f"min/avg/max across {reps} runs ({m} metric(s) per run):"]
            for pos in range(m):
                col = [per_run[r][pos] for r in range(reps)]
                runs_str = ", ".join(f"{v:.4g}" for v in col)
                lines.append(
                    f"  m{pos + 1}: min={min(col):.4g} avg={sum(col) / reps:.4g} "
                    f"max={max(col):.4g}  [{runs_str}]"
                )
            body = "\n".join(lines)
        else:
            body = (f"repeats recorded as-is across {reps} runs; output is not a "
                    f"fixed scalar set (metric counts={sorted(counts)}) — compare "
                    f"the run sections above directly.")
        out.append({"phase": "aggregate", "label": cmd.format(**SUBST),
                    "exit": 0, "elapsed": 0.0, "stdout": body, "stderr": ""})
    return out


def run_case(case_id, name, commands, memory=False, requires_env=None,
             cmd_timeout=None, repeats=1, pin=True, user=None):
    """Run one case in a fresh sandbox.

    memory:  run cold -> warmup -> warm in the same sandbox (uffd cold-start cost).
    repeats: run the whole command list this many times so variance is visible;
             a min/avg/max summary is appended per command. Use for µs-level
             latency cases (B1/B2/B3/B5/B8). Ignored when memory is set.
    cmd_timeout: per-command timeout in seconds (default DURATION+90); raise it
             for long benchmarks like UnixBench (B10).
    pin:     pin the shell (and all children) to PIN_CORE so single-worker cases
             never migrate across vCPUs (default True). Multicore cases (A1/A2/A4/A7/B10)
             should pass pin=False.
    user:    run the measured commands as this sandbox user (e2b only); needed when a
             case writes somewhere only root may (e.g. metadata tests on / use "root").
    """
    # Skip cleanly when a required env var (e.g. a network server) is unset.
    missing = [e for e in (requires_env or []) if not os.environ.get(e)]
    if missing:
        write_result(case_id, name, "-", [], 0.0,
                     status=f"skipped (set {', '.join(missing)})")
        print(f"[{case_id}] {name}: SKIPPED (missing {', '.join(missing)})")
        return

    if BACKEND == "local":
        memory = False  # host has no uffd; cold/warmup/warm is meaningless
    if memory:
        repeats = 1

    # Which core to pin to (None disables). Individual cases can opt out.
    pin_core = None if not pin or not PIN_CORE else PIN_CORE

    # Many benchmarks (lmbench lat_*, 7z, openssl speed) ignore DURATION and run
    # to their own completion, so keep a 5-min floor regardless of DURATION.
    cmd_timeout = cmd_timeout or max(DURATION + 90, 300)
    # Sandbox lifetime: generous headroom over every command we will run.
    n_runs = 2 if memory else 1
    timeout = max(300, (cmd_timeout + 15) * len(commands) * n_runs * repeats + 120)
    timeout = min(timeout, SANDBOX_MAX_TIMEOUT)

    # Executor: a fresh sandbox (e2b) or a local subprocess (host baseline).
    # The hook runs once as root WITHOUT measurement; benchmarks run WITH measurement.
    if BACKEND == "local":
        sbx = None
        sandbox_id = "local"
        def exec_one(c, t, p, measure=True):
            return run_cmd_local(c, t, p, pin_core=pin_core, measure=measure)
    else:
        sbx = make_sandbox(timeout)
        sandbox_id = getattr(sbx, "sandbox_id", "?")
        def exec_one(c, t, p, measure=True):
            return run_cmd(sbx, c, t, p, pin_core=pin_core, measure=measure, user=user)

    sections = []
    runs = []  # repeats x commands grid of sections, for aggregation
    start = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()
    try:
        # Optional one-shot root setup hook (e.g. remount noatime / tune sysfs),
        # applied to this fresh sandbox before any measurement. Recorded so we
        # can confirm it took effect; its exit feeds into the case status.
        if HOOK:
            if BACKEND == "local":
                h = run_cmd_local(HOOK, 60, f"{case_id} [hook]", do_format=False)
            else:
                h = run_cmd(sbx, HOOK, 60, f"{case_id} [hook]", user="root", do_format=False)
            h["phase"] = "hook"
            sections.append(h)
        if memory:
            for c in commands:                       # 1. cold (first access, uffd)
                s = exec_one(c, cmd_timeout, f"{case_id} [cold]"); s["phase"] = "cold"; sections.append(s)
            w = exec_one(warmup_command(), 300, f"{case_id} [warmup]")  # 2. warm up guest RAM
            w["phase"] = "warmup"; sections.append(w)
            for c in commands:                       # 3. warm (steady state)
                s = exec_one(c, cmd_timeout, f"{case_id} [warm]"); s["phase"] = "warm"; sections.append(s)
        else:
            for r in range(repeats):
                phase = f"run {r + 1}/{repeats}" if repeats > 1 else "run"
                row = []
                for c in commands:
                    s = exec_one(c, cmd_timeout, f"{case_id} [{phase}]")
                    s["phase"] = phase
                    sections.append(s); row.append(s)
                runs.append(row)
        # Record the filesystem the tests ran on (mount opts + ext4 features) so
        # results stay interpretable. Read-only, as root, unmeasured; never fails.
        if BACKEND == "local":
            fi = run_cmd_local(FSINFO_CMD, 60, f"{case_id} [fsinfo]", do_format=False)
        else:
            fi = run_cmd(sbx, FSINFO_CMD, 60, f"{case_id} [fsinfo]", user="root", do_format=False)
        fi["phase"] = "fsinfo"
        sections.append(fi)
    finally:
        if sbx is not None:
            try:
                sbx.kill()
            except Exception:
                pass

    total = round(time.time() - start, 2)
    # Any non-zero exit (incl. -1 timeout) is a failure; don't use max() — 0 > -1.
    # The informational fsinfo probe never counts toward the case status.
    fails = [s["exit"] for s in sections if s["exit"] != 0 and s.get("phase") != "fsinfo"]
    status = "ok" if not fails else f"completed-with-errors (exits {fails})"
    agg = aggregate_runs(commands, runs) if repeats > 1 else []
    health = health_section(sections)  # per-run steal/iowait/cs verdict
    path = write_result(case_id, name, sandbox_id, sections + agg + health, total,
                        status=status, started=started_iso)
    print(f"[{case_id}] {name}: done in {total}s "
          f"({'OK' if not fails else 'FAILED ' + str(fails)}) -> {os.path.basename(path)}")
    # Surface failures via exit code so run_all.py (and CI) see a real non-zero rc.
    if fails:
        sys.exit(1)
