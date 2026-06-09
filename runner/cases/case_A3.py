import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import run_case

if __name__ == "__main__":
    run_case("A3", "cpu-crypto", [
        "openssl speed -elapsed -evp aes-256-gcm",
        "openssl speed -elapsed sha256",
        "openssl speed -elapsed rsa2048",
    ])
