import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A9", "storage-rand", [
        "mkdir -p {storage_dir}; "
        "fio --directory={storage_dir} --name=randrw --rw=randrw --bs=4k "
        "--iodepth=32 --ioengine=libaio --size=1G --direct=1 --numjobs=1 "
        "--runtime={duration} --time_based=1 --group_reporting=1",
    ], user="root")
