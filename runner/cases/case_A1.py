import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A1", "cpu-int", [
        "sysbench cpu --cpu-max-prime=20000 --threads=1 --time={duration} run",
        "7z b",
    ], pin=False)  # 7z b is a multicore benchmark; do not pin to one core
