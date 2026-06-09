import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: chmod
    run_case("B16", "fs-chmod", [
        "stress-ng --temp-path / --chmod 1 --metrics-brief --no-rand-seed -t {duration}",
    ], user="root")
