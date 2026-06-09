import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A2", "cpu-float", [
        "stress-ng --temp-path /tmp --cpu 1 --cpu-method matrixprod -t {duration} --metrics-brief",
        "python3 -c \"import time,numpy as np; n=2048; a=np.random.rand(n,n); b=np.random.rand(n,n); a@b; t=time.time(); a@b; print('gflops=%.2f'%(2*n**3/(time.time()-t)/1e9))\"",
    ], pin=False)  # numpy GEMM uses multithreaded BLAS; do not pin to one core
