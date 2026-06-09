import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # lat_proc's exec/shell sub-tests exec a tiny helper named "hello" that it
    # locates as getcwd()/hello. The SDK's CWD is /tmp (no hello there), so exec
    # silently fails and shell prints "/tmp/hello: not found". cd into lat_proc's
    # own dir (a symlink to lmbench's bin/, where hello lives) so getcwd()/hello
    # resolves and exec/shell measure real fork+execve / fork+sh costs.
    run_case("B2", "proc-create", [
        'cd "$(dirname "$(readlink -f "$(command -v lat_proc)")")"; '
        'for k in fork exec shell; do echo "=== $k ==="; lat_proc $k; done',
        "stress-ng --temp-path /tmp --fork 1 -t {duration} --metrics-brief",
    ], repeats=3)
