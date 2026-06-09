import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

# DNS always; HTTP connection-rate (wrk) only if WRK_TARGET is set.
if __name__ == "__main__":
    run_case("A15", "net-conn-dns", [
        'for i in 1 2 3 4 5; do dig +noall +stats example.com | grep "Query time"; done',
        'if [ -n "{wrk_target}" ]; then wrk -c100 -t4 -d{duration}s {wrk_target}; '
        'else echo "WRK_TARGET unset, skipping wrk"; fi',
    ])
