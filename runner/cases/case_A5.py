import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A5", "mem-bandwidth", [
        "stress-ng --temp-path /tmp --stream 1 -t {duration} --metrics-brief",
        "sysbench memory --memory-block-size=1M --memory-total-size=10G --threads=1 run",
    ])
