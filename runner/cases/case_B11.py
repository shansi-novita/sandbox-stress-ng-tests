import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: directory create/remove (mkdir/rmdir)
    run_case("B11", "fs-mkdir", [
        "stress-ng --temp-path / --dir 1 --metrics-brief --no-rand-seed -t {duration}",
    ], user="root")
