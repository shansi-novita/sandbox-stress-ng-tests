import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("B5", "ipc-pipe", [
        "bw_pipe",
        "stress-ng --temp-path /tmp --pipe 1 -t {duration} --metrics-brief",
    ], repeats=3)
