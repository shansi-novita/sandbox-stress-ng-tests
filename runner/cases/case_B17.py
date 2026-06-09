import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: chown
    run_case("B17", "fs-chown", [
        "stress-ng --temp-path / --chown 1 --metrics-brief --no-rand-seed -t {duration}",
    ], user="root")
