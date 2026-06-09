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
  STORAGE_DIR       where storage tests write inside the sandbox (default: /tmp/perf-io)
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
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/tmp/perf-io")
STREAM = os.environ.get("STREAM", "1") != "0"  # live-stream output to terminal
# Optional shell command run ONCE as root in each fresh sandbox (or the local
# container) BEFORE the case commands — for tuning the environment, e.g.
#   SANDBOX_HOOK='mount -o remount,noatime /'
#   SANDBOX_HOOK='DEV=$(basename $(findmnt -no SOURCE /)); echo 64 > /sys/fs/ext4/$DEV/inode_readahead_blks'
# Recorded as a [hook] section; its exit code counts toward the case status so a
# failed tuning step is visible. Empty = disabled. NOT placeholder-substituted.
HOOK = os.environ.get("SANDBOX_HOOK", "").strip()

# e2b.dev rejects Sandbox.create timeouts greater than 1 hour; cap the computed
# sandbox lifetime so long cases (e.g. B10 UnixBench) still start instead of 400ing.
SANDBOX_MAX_TIMEOUT = 3600

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


def run_cmd(sbx, cmd, cmd_timeout, prefix="", user=None, do_format=True):
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
    out_buf, err_buf = [], []

    def on_out(d):
        out_buf.append(d)
        if STREAM:
            sys.stdout.write(d); sys.stdout.flush()

    def on_err(d):
        err_buf.append(d)
        if STREAM:
            sys.stderr.write(d); sys.stderr.flush()

    start = time.time()
    try:
        kwargs = {"timeout": cmd_timeout, "on_stdout": on_out, "on_stderr": on_err}
        if user:
            kwargs["user"] = user
        res = sbx.commands.run(cmd, **kwargs)
        exit_code = res.exit_code
    except Exception as e:  # command error/timeout: capture what streamed, don't abort
        exit_code = -1
        msg = f"\n{type(e).__name__}: {e}"
        err_buf.append(msg)
        if STREAM:
            sys.stderr.write(msg); sys.stderr.flush()
    return {
        "label": cmd,
        "exit": exit_code,
        "elapsed": round(time.time() - start, 2),
        "stdout": "".join(out_buf),
        "stderr": "".join(err_buf),
    }


def run_cmd_local(cmd, cmd_timeout, prefix="", do_format=True):
    """Run one command locally via bash (BACKEND=local, inside the host container).

    stderr is merged into stdout so the live stream and captured buffer keep
    output ordering. A watchdog timer enforces cmd_timeout even if the command
    hangs while producing no output. Mirrors run_cmd's return shape.
    """
    if do_format:
        cmd = cmd.format(**SUBST)
    if STREAM:
        print(f"\n>>> {prefix} $ {cmd}", flush=True)
    buf = []
    timed_out = {"v": False}
    start = time.time()
    try:
        p = subprocess.Popen(["bash", "-lc", cmd], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        timer = threading.Timer(cmd_timeout, lambda: (timed_out.__setitem__("v", True), p.kill()))
        timer.start()
        try:
            for line in p.stdout:
                buf.append(line)
                if STREAM:
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
    return {
        "label": cmd,
        "exit": exit_code,
        "elapsed": round(time.time() - start, 2),
        "stdout": "".join(buf),
        "stderr": "",
    }


def write_result(case_id, name, sandbox_id, sections, total_elapsed, status="ok"):
    path = os.path.join(batch_dir(), f"{case_id}-{name}.txt")
    with open(path, "w") as f:
        f.write(f"# case      : {case_id} {name}\n")
        f.write(f"# status    : {status}\n")
        f.write(f"# template  : {TEMPLATE}\n")
        f.write(f"# domain    : {DOMAIN or 'e2b.dev'}\n")
        f.write(f"# sandbox   : {sandbox_id}\n")
        f.write(f"# duration  : {DURATION}s/test\n")
        f.write(f"# finished  : {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"# total_s   : {total_elapsed}\n")
        for s in sections:
            f.write("\n" + "=" * 70 + "\n")
            f.write(f"[{s.get('phase', 'run')}] $ {s['label']}\n")
            f.write(f"exit={s['exit']}  elapsed={s['elapsed']}s\n")
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
             cmd_timeout=None, repeats=1):
    """Run one case in a fresh sandbox.

    memory:  run cold -> warmup -> warm in the same sandbox (uffd cold-start cost).
    repeats: run the whole command list this many times so variance is visible;
             a min/avg/max summary is appended per command. Use for µs-level
             latency cases (B1/B2/B3/B5/B8). Ignored when memory is set.
    cmd_timeout: per-command timeout in seconds (default DURATION+90); raise it
             for long benchmarks like UnixBench (B10).
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

    # Many benchmarks (lmbench lat_*, 7z, openssl speed) ignore DURATION and run
    # to their own completion, so keep a 5-min floor regardless of DURATION.
    cmd_timeout = cmd_timeout or max(DURATION + 90, 300)
    # Sandbox lifetime: generous headroom over every command we will run.
    n_runs = 2 if memory else 1
    timeout = max(300, (cmd_timeout + 15) * len(commands) * n_runs * repeats + 120)
    timeout = min(timeout, SANDBOX_MAX_TIMEOUT)

    # Executor: a fresh sandbox (e2b) or a local subprocess (host baseline).
    if BACKEND == "local":
        sbx = None
        sandbox_id = "local"
        def exec_one(c, t, p):
            return run_cmd_local(c, t, p)
    else:
        sbx = make_sandbox(timeout)
        sandbox_id = getattr(sbx, "sandbox_id", "?")
        def exec_one(c, t, p):
            return run_cmd(sbx, c, t, p)

    sections = []
    runs = []  # repeats x commands grid of sections, for aggregation
    start = time.time()
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
    finally:
        if sbx is not None:
            try:
                sbx.kill()
            except Exception:
                pass

    total = round(time.time() - start, 2)
    # Any non-zero exit (incl. -1 timeout) is a failure; don't use max() — 0 > -1.
    fails = [s["exit"] for s in sections if s["exit"] != 0]
    status = "ok" if not fails else f"completed-with-errors (exits {fails})"
    agg = aggregate_runs(commands, runs) if repeats > 1 else []
    path = write_result(case_id, name, sandbox_id, sections + agg, total, status=status)
    print(f"[{case_id}] {name}: done in {total}s "
          f"({'OK' if not fails else 'FAILED ' + str(fails)}) -> {os.path.basename(path)}")
    # Surface failures via exit code so run_all.py (and CI) see a real non-zero rc.
    if fails:
        sys.exit(1)
