import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    # uffd first-touch cost. Sandboxes load snapshot RAM lazily via userfaultfd:
    # the first WRITE to an unloaded guest frame traps to the host handler (fetch
    # the page), later accesses are plain RAM. We measure that directly:
    #   read_cold  : read a fresh mmap region once. Untouched anon pages map to the
    #                shared zero-page on read -> NO real frame, little/no uffd. This
    #                is the control showing reads are NOT a valid uffd path.
    #   write_cold : first write over a fresh region -> allocates frames -> uffd.
    #   write_warm : second write over the same region -> pure RAM.
    # first_touch delta = write_cold - write_warm (minor-fault + uffd combined; to
    # isolate the pure uffd part, compare this delta against BACKEND=local on the
    # same host, which has no uffd). mmap (not bytearray) avoids pre-faulting pages.
    run_case("A16", "mem-uffd", [
        '''python3 -c "
import mmap, time, os
mb = int(os.environ.get('UFFD_MB', '512'))
ps = os.sysconf('SC_PAGE_SIZE')
n = mb * 1024 * 1024
pages = n // ps
mr = mmap.mmap(-1, n)
mw = mmap.mmap(-1, n)
def rd(m):
    s = 0
    for i in range(0, n, ps):
        s += m[i]
    return s
def wr(m):
    for i in range(0, n, ps):
        m[i] = 1
t0 = time.perf_counter(); rd(mr); read_cold = time.perf_counter() - t0
t0 = time.perf_counter(); wr(mw); write_cold = time.perf_counter() - t0
t0 = time.perf_counter(); wr(mw); write_warm = time.perf_counter() - t0
d = write_cold - write_warm
print('mem=%dMB pages=%d page=%dB' % (mb, pages, ps))
print('read_cold_s=%.4f  (fresh anon read -> zero-page, little/no uffd)' % read_cold)
print('write_cold_s=%.4f write_warm_s=%.4f first_touch_delta_s=%.4f' % (write_cold, write_warm, d))
print('first_touch_per_page_us=%.3f cold_pages_per_s=%.0f' % (d / pages * 1e6, pages / write_cold))
"''',
    ])
