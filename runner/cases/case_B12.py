import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: hardlink create/remove
    run_case("B12", "fs-link", [
        "stress-ng --temp-path / --link 1 --metrics-brief --no-rand-seed -t {duration}",
    ], user="root")
