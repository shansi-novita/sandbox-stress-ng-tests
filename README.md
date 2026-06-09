# Sandbox Performance Benchmarks

Guest-side benchmark harness for systematically evaluating E2B sandbox (Firecracker microVM) performance.
See [`sandbox-perf-testplan.md`](./sandbox-perf-testplan.md) for the full test matrix (facets A–D).

This directory implements **Phase 1–3** (low-level resources + virtualization overhead).
Phases 4–6 (lifecycle, real workloads, scale) are tracked in the plan but not yet scripted.

## Layout

```
sandbox-perf-testplan.md   # the full matrix + methodology
lib/common.sh              # shared harness: logging, metric CSV, tool detection
00-setup.sh                # Phase 1: install tools (apt/apk) + build lmbench/UnixBench
01-baseline-env.sh         # Phase 1: capture environment fingerprint -> env.txt
02-cpu.sh                  # Phase 2: A1-A4  CPU int/float/crypto/scaling
02-memory.sh               # Phase 2: A5-A7  bandwidth/latency/TLB
02-storage.sh              # Phase 2: A8-A12 seq/rand IO, small files, fsync, latency
02-network.sh              # Phase 2: A13-A15 bandwidth/latency/DNS (needs peer for A13/A14)
03-syscall-proc-ipc.sh     # Phase 3: B1-B10 syscall/proc/ctx/IPC/sched/composite
run-all.sh                 # orchestrate phases 1-3, print summary
results/<run-id>/          # per-run output (metrics.csv + raw/ + env.txt)
```

## Quick start

Run **inside the sandbox** (the microVM guest):

```bash
cd tests/perf

# Full run: install tools, capture env, run phases 2 & 3
./run-all.sh

# Smoke test (short durations), no tool install
DURATION=10 SKIP_SETUP=1 ./run-all.sh

# Tag a baseline run on bare metal, then a sandbox run, then diff
LABEL=baremetal ./run-all.sh        # on host
LABEL=sandbox   ./run-all.sh        # in microVM
diff <(sort results/*baremetal*/metrics.csv) <(sort results/*sandbox*/metrics.csv)
```

## Tunables (env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `DURATION` | `30` | seconds per stress-ng / fio time-based test |
| `STORAGE_DIR` | `/tmp/perf-io` | where storage tests write (point at the FS you want to measure) |
| `RUN_ID` | timestamp | shared id for one run (set by `run-all.sh`) |
| `LABEL` | `run` | tag embedded in run id, e.g. `baremetal` / `sandbox` |
| `SKIP_SETUP` | `0` | skip `00-setup.sh` when tools already installed |
| `SKIP_SOURCE` | `0` | in setup, skip building lmbench/UnixBench from source |
| `IPERF_SERVER` | _(unset)_ | enable A13: host running `iperf3 -s` |
| `NETPERF_SERVER` | _(unset)_ | enable A14: host running `netserver` |
| `WRK_TARGET` | _(unset)_ | enable A15 HTTP rps: a URL to load |

## Output

Every test appends one row to `results/<run-id>/metrics.csv`:

```
phase,test_id,name,metric,value,unit,note
```

Raw tool output is kept under `results/<run-id>/raw/<test_id>.txt` for verification, and the
environment fingerprint (CPU/mem/kernel/cgroup/virt/tool versions) in `results/<run-id>/env.txt`.

Tests whose tools are missing or whose network peer is unset are recorded as `skip` rows rather
than silently dropped, so coverage gaps are always visible.

## Notes

- Designed to degrade gracefully: a missing tool skips its test, it does not abort the run.
- `--direct=1` fio jobs bypass page cache to measure the real storage backend (NBD/ext4); run
  the **cold** path deliberately, and a second time for the **warm** (page-cached) path.
- Always pair a `baremetal` run with a `sandbox` run — absolute numbers mean little; the
  virtualization-overhead **percentage** is the headline result.
- Report tail latency (P99), not just averages — see methodology in the test plan.
