import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

# UnixBench's Run lives under the template's UnixBench dir, but the Dockerfile
# ENV (UNIXBENCH_DIR) does not reliably propagate into the sandbox command shell
# -- an empty $UNIXBENCH_DIR makes `cd` fall back to $HOME and Run "disappears".
# Locate Run dynamically instead. Each pass runs a few minutes; cmd_timeout stays
# under the 1h sandbox-lifetime cap enforced in common.py.
RUN = ("D=$(dirname \"$(find /opt -name Run -path '*UnixBench*' 2>/dev/null | head -1)\"); "
       'cd "$D" && ./Run')

if __name__ == "__main__":
    # -i 1 runs each sub-benchmark once instead of UnixBench's default (3-10
    # iterations), cutting a full pass from ~10min to ~2-3min.
    run_case("B10", "composite", [
        RUN + " -i 1 -c 1",
        RUN + " -i 1 -c $(nproc)",
    ], cmd_timeout=900)
