import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A11", "storage-fsync", [
        "mkdir -p {storage_dir}; "
        "fio --directory={storage_dir} --name=fsync --rw=write --bs=4k --fsync=1 "
        "--size=512M --numjobs=1 --runtime={duration} --time_based=1 --group_reporting=1",
    ])
