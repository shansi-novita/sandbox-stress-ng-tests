import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # cyclictest needs CAP_SYS_NICE / SCHED_FIFO, which sandboxes don't grant, so
    # it always aborts there. Measure scheduling jitter the unprivileged, cyclictest
    # -like way: spin a tight loop reading a monotonic clock and record the gap
    # between consecutive reads. Gaps are normally nanoseconds; when the scheduler
    # preempts this thread the gap spikes, so the tail (p99.9 / max) is the
    # involuntary-preemption latency the sandbox imposes.
    run_case("B9", "sched-jitter", [
        '''python3 -c "
import time
n = 2000000
g = [0.0] * n
prev = time.perf_counter()
for i in range(n):
    now = time.perf_counter()
    g[i] = (now - prev) * 1e6
    prev = now
g.sort()
avg = sum(g) / n
print('samples=%d (tight-loop preemption gaps)' % n)
print('gap_us min=%.3f avg=%.3f p50=%.3f p99=%.3f p99.9=%.3f max=%.3f' % (g[0], avg, g[n//2], g[int(n*0.99)], g[int(n*0.999)], g[-1]))
"''',
    ])
