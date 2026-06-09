import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: utime (timestamp updates)
    run_case("B18", "fs-utime", [
        "stress-ng --temp-path /tmp --utime 1 --metrics-brief --no-rand-seed -t 10",
    ])
