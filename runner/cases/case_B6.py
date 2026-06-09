import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("B6", "ipc-futex", [
        "stress-ng --temp-path /tmp --futex 1 -t {duration} --metrics-brief",
    ])
