import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

_FIO = ("fio --directory={storage_dir} --output-format=normal --group_reporting=1 "
        "--runtime={duration} --time_based=1 --numjobs=1 --direct=1 --size=1G")

if __name__ == "__main__":
    run_case("A8", "storage-seq", [
        "mkdir -p {storage_dir}; " + _FIO + " --name=seqread --rw=read --bs=1M",
        _FIO + " --name=seqwrite --rw=write --bs=1M",
    ])
