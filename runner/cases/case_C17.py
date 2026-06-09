import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # filesystem metadata on TMPFS: chown.
    #
    # Same stress-ng workload as B17 (fs-chown), but --temp-path points at a tmpfs
    # instead of the rootfs. tmpfs has no block device, no journal (jbd2), no NBD
    # userspace round-trip and no COW backing layer, so C17 vs B17 isolates the
    # storage-stack cost from the pure syscall + VM-exit cost.
    #
    # Cross-backend tmpfs: in the sandbox, root mounts tmpfs directly (CAP_SYS_ADMIN
    # is available, like the remount hooks). In BACKEND=local the Docker container
    # is unprivileged, so run-host.sh pre-mounts the same path via `docker --tmpfs`;
    # the `grep /proc/mounts` guard makes the mount idempotent and skips it there.
    # `stat -f -c %T` records the actual backing fs so a silent fallback to a plain
    # directory (which would invalidate the comparison) is visible in the result.
    run_case("C17", "fs-chown-tmpfs", [
        "TMPD=/mnt/tmpfsbench; mkdir -p $TMPD; "
        "grep -q \" $TMPD \" /proc/mounts || mount -t tmpfs -o size=1g none $TMPD; "
        "echo \"temp-path backing fs: $(stat -f -c %T $TMPD)\"; "
        "stress-ng --temp-path $TMPD --chown 1 --metrics-brief --no-rand-seed -t {duration}",
    ], user="root")
