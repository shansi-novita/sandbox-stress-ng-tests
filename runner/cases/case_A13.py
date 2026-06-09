import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A13", "net-bw", [
        "iperf3 -c {iperf_server} -t {duration} -J",
    ], requires_env=["IPERF_SERVER"])
