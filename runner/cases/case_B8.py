import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("B8", "pagefault-mmap", [
        "tf=$(mktemp); dd if=/dev/zero of=$tf bs=1M count=64 2>/dev/null; "
        "lat_pagefault $tf; rm -f $tf",
        "stress-ng --temp-path /tmp --mmap 1 -t {duration} --metrics-brief",
    ], repeats=3)
