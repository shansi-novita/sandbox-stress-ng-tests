import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("B1", "syscall-lat", [
        'for op in null read write stat open; do echo "=== $op ==="; lat_syscall $op; done',
    ], repeats=3)
