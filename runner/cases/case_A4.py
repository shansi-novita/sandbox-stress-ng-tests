import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case, DURATION

if __name__ == "__main__":
    # Sweep thread counts 1 / 2 / nproc/2 / nproc / 2*nproc. The 2*nproc point is
    # oversubscription: it reveals whether the 2 "vCPUs" are real cores (throughput
    # keeps scaling/plateaus) or a CPU-quota cap (throughput stays flat past nproc,
    # as we saw on OCI where 2 threads barely beat 1). sort -un dedups the set
    # (on a 2-vCPU box this yields 1 2 4). The single command runs sysbench once
    # per distinct count (<=5), so budget ~6x DURATION for the timeout.
    run_case("A4", "cpu-scaling", [
        'for t in $(printf "%s\\n" 1 2 $(($(nproc)/2)) $(nproc) $(($(nproc)*2)) | sort -un); do echo "=== threads=$t ==="; '
        'sysbench cpu --cpu-max-prime=20000 --threads=$t --time={duration} run '
        '| grep -E "events per second"; done',
    ], cmd_timeout=DURATION * 6 + 120)
