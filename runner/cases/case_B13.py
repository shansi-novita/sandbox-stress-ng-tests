import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: symlink create/remove
    run_case("B13", "fs-symlink", [
        "stress-ng --temp-path / --symlink 1 --metrics-brief --no-rand-seed -t {duration}",
    ], user="root")
