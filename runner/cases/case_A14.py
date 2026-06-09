import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A14", "net-rtt", [
        "netperf -H {netperf_server} -t TCP_RR -l {duration}",
    ], requires_env=["NETPERF_SERVER"])
