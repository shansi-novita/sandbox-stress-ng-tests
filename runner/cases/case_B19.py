import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: readdir (getdents)
    run_case("B19", "fs-getdent", [
        "stress-ng --temp-path /tmp --getdent 1 --metrics-brief --no-rand-seed -t 10",
    ])
