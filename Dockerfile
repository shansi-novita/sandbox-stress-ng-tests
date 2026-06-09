FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    # ===== 压测工具 =====
    stress-ng \
    fio \
    sysbench \
    p7zip-full \
    openssl \
    ioping \
    iperf3 \
    netperf \
    wrk \
    rt-tests \
    # ===== Python（A2b numpy GEMM + fio JSON 解析）=====
    python3 \
    python3-numpy \
    # ===== syscall/性能分析 =====
    strace \
    ltrace \
    linux-tools-generic \
    bpfcc-tools \
    bpftrace \
    # ===== 系统观测 =====
    sysstat \
    procps \
    iotop \
    htop \
    # ===== 网络工具 =====
    iputils-ping \
    iproute2 \
    dnsutils \
    # ===== 文件系统工具 =====
    e2fsprogs \
    xfsprogs \
    util-linux \
    coreutils \
    findutils \
    # ===== 构建 lmbench / UnixBench 所需 =====
    build-essential \
    git \
    libtirpc-dev \
    # ===== 辅助 =====
    time \
    bc \
    jq \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 链接 perf 到 PATH
RUN ln -sf /usr/lib/linux-tools/*/perf /usr/local/bin/perf || true

# ===== 源码构建 lmbench（B1 syscall / B2 proc / B3 ctx / B5 pipe / B8 pagefault / A6 内存延迟）=====
# 包管理器版本的二进制路径/命名不稳定，源码构建后把 lat_*/bw_* 软链到 PATH，便于脚本直接调用。
RUN git clone --depth 1 https://github.com/intel/lmbench /opt/lmbench \
 && make -C /opt/lmbench build >/dev/null 2>&1 || true \
 && for b in $(find /opt/lmbench/bin -maxdepth 2 -type f -perm -u+x 2>/dev/null); do \
        ln -sf "$b" /usr/local/bin/; \
    done \
 && (command -v lat_syscall >/dev/null && echo "lmbench OK" || echo "WARN: lmbench binaries missing")

# ===== 源码构建 UnixBench（B10 综合分）=====
RUN git clone --depth 1 https://github.com/kdlucas/byte-unixbench /opt/byte-unixbench \
 && make -C /opt/byte-unixbench/UnixBench >/dev/null 2>&1 || true
# 让 03-syscall-proc-ipc.sh 找到 UnixBench
ENV UNIXBENCH_DIR=/opt/byte-unixbench/UnixBench

# ===== 拷入性能测试脚本（Phase 1-3）=====
# 假设 build context 为本目录（tests/perf）。
COPY lib/        /test/lib/
COPY *.sh        /test/
COPY *.md        /test/
RUN chmod +x /test/*.sh /test/lib/*.sh

WORKDIR /test
CMD ["/bin/bash"]
