import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata: file (dentry) create/remove
    run_case("B14", "fs-dentry", [
        "stress-ng --temp-path /tmp --dentry 1 --metrics-brief --no-rand-seed -t 15",
    ])
