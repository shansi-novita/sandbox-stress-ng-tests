"""Master orchestrator: run every perf case in its own sandbox, one batch dir.

Each case script (cases/case_<ID>.py) is run as a separate subprocess so cases
stay fully isolated. All share one BATCH_DIR. Progress is recorded to the console
and to progress.log; a summary.txt indexes the per-case result files.

Usage:
  python3 run_all.py                # all 25 cases
  python3 run_all.py A1 A6 B10      # only the listed cases
  DURATION=10 python3 run_all.py    # shorter tests
"""

import os
import subprocess
import sys
import time
from datetime import datetime

#CASES = [f"A{i}" for i in range(1, 17)] + [f"B{i}" for i in range(1, 20)]
CASES = [f"B{i}" for i in range(1, 20)]

HERE = os.path.dirname(os.path.abspath(__file__))
CASES_DIR = os.path.join(HERE, "cases")


def read_result(batch, cid):
    """Return (status, result_file) read from the case's result file header.

    The case script writes the true per-command status (ok / skipped /
    completed-with-errors) into the `# status` header line. Trust that over the
    subprocess exit code, which can be 0 even when individual commands failed.
    """
    for n in sorted(os.listdir(batch)):
        if n.startswith(cid + "-") and n.endswith(".txt"):
            try:
                with open(os.path.join(batch, n)) as f:
                    for line in f:
                        if line.startswith("# status"):
                            return line.split(":", 1)[1].strip(), n
            except OSError:
                pass
            return None, n
    return None, "-"


def main():
    selected = [c.upper() for c in sys.argv[1:]] or CASES

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = os.environ.get("RESULTS_ROOT") or os.path.join(HERE, "results")
    batch = os.path.join(root, stamp)
    os.makedirs(batch, exist_ok=True)
    env = dict(os.environ, BATCH_DIR=batch)

    progress = os.path.join(batch, "progress.log")
    results = []

    def log(msg):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with open(progress, "a") as f:
            f.write(line + "\n")

    log(f"batch start: {len(selected)} case(s) -> {batch}")
    log(f"template={env.get('PERF_TEMPLATE', 'perf-bench')} "
        f"domain={env.get('E2B_DOMAIN', 'e2b.dev')} duration={env.get('DURATION', '30')}s")

    for cid in selected:
        script = os.path.join(CASES_DIR, f"case_{cid}.py")
        if not os.path.isfile(script):
            log(f"{cid}: MISSING script, skipped")
            results.append((cid, "missing", 0))
            continue
        log(f"{cid}: running...")
        start = time.time()
        rc = subprocess.run([sys.executable, script], env=env).returncode
        dur = round(time.time() - start, 1)
        # Prefer the true status from the result file; fall back to the exit code
        # when no file was written (e.g. the sandbox failed to start).
        file_status, rf = read_result(batch, cid)
        status = file_status or ("ok" if rc == 0 else f"rc={rc}")
        log(f"{cid}: {status} ({dur}s)")
        results.append((cid, status, dur, rf))

    # summary index (status is variable-length, so keep it last)
    with open(os.path.join(batch, "summary.txt"), "w") as f:
        f.write(f"batch: {stamp}\n")
        f.write(f"{'case':<6}{'sec':<8}{'result_file':<28}status\n")
        for cid, status, dur, rf in results:
            f.write(f"{cid:<6}{dur:<8}{rf:<28}{status}\n")

    log(f"batch done: results in {batch}")
    print(f"\nsummary: {os.path.join(batch, 'summary.txt')}")


if __name__ == "__main__":
    main()
