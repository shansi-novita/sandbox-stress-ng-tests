import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: rename
    run_case("B15", "fs-rename", [
        "stress-ng --temp-path /tmp --rename 1 --metrics-brief --no-rand-seed -t 15",
    ])
