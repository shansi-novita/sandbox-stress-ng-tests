import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A12", "storage-iolat", [
        "mkdir -p {storage_dir}; ioping -c 30 -D {storage_dir}",
    ])
