import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # lat_mem_rd ignores DURATION and runs to completion. By default it repeats
    # each size point ~11x (lmbench TRIES); in a sandbox that is ~12s/point, so the
    # full curve never finishes. "-N 1" = one repetition/point (~11x faster,
    # slightly noisier but the cache steps stay obvious).
    # Sweep up to 512MB: AMD EPYC L3 can be 256-384MB, so a 128MB working set still
    # lives entirely in L3 there (we measured ~25ns "DRAM" on EPYC -> not DRAM at
    # all). 512MB exceeds even large L3 so the tail reflects real memory latency.
    run_case("A6", "mem-latency", [
        "lat_mem_rd -N 1 512 512",
    ], cmd_timeout=900, repeats=2)
