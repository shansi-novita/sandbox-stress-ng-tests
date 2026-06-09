import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("B4", "thread-create", [
        "stress-ng --temp-path /tmp --pthread 1 -t {duration} --metrics-brief",
    ], repeats=3)
