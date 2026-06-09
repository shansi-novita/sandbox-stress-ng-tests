import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("B3", "ctx-switch", [
        "lat_ctx -s 0 2 4 8 16",
        "stress-ng --temp-path /tmp --switch 1 -t {duration} --metrics-brief",
    ], repeats=3)
