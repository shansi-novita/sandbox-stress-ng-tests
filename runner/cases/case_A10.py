import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

# 5000-small-file tar extract: mkdir+create+write storm (pnpm/npm-like).
if __name__ == "__main__":
    run_case("A10", "storage-smallfiles", [
        'd={storage_dir}/sf; rm -rf $d; mkdir -p $d/src; '
        '(cd $d/src && for i in $(seq 1 5000); do echo x > f$i; done); '
        'tar cf $d/sf.tar -C $d/src .; sync; mkdir -p $d/out; '
        'start=$(date +%s.%N); tar xf $d/sf.tar -C $d/out; sync; end=$(date +%s.%N); '
        'echo "extract_seconds=$(echo "$end - $start" | bc)"; '
        'echo "files_per_s=$(echo "scale=0; 5000/($end - $start)" | bc)"; rm -rf $d',
    ], user="root")
