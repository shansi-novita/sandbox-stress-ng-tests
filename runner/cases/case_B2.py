import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # lat_proc's exec/shell sub-tests exec a helper named "hello" located at
    # getcwd()/hello (NOT via $PATH). Relying on lmbench's own hello is fragile
    # (may not be built/symlinked in the image). Instead compile a trivial hello
    # ourselves with the gcc that's already in the image, in /tmp (always writable
    # and the cwd lat_proc sees), so exec/shell measure real fork+execve / fork+sh.
    run_case("B2", "proc-create", [
        'cd /tmp && printf "int main(){return 0;}" > h.c '
        '&& cc -o hello h.c 2>/dev/null || printf "#!/bin/sh\\nexit 0\\n" > hello; '
        'chmod +x hello; '
        'for k in fork exec shell; do echo "=== $k ==="; lat_proc $k; done; '
        'rm -f hello h.c',
        "stress-ng --temp-path /tmp --fork 1 -t {duration} --metrics-brief",
    ], repeats=3)
