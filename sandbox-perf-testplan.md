# Sandbox Performance Test Plan

系统化评测 E2B 沙箱（Firecracker microVM）性能的分层测试矩阵。
目标：**性能面全覆盖**，每个用例明确「测什么 → 工具 → 关键指标」，可作为评测清单跟踪执行。

> 大多数 A/B/D 类用例在**沙箱内部（guest）**执行；C 类生命周期用例在**编排层（host/API）**埋点采集。

---

## 0. 总体框架

```
A. 底层资源性能（裸性能）   —— CPU / 内存 / 存储 / 网络
B. 系统/虚拟化开销           —— syscall / 进程调度 / IPC / 上下文切换
C. 沙箱生命周期（E2B 专属） —— 冷启动 / 快照恢复 / 模板加载 / 销毁
D. 真实负载 & 规模化         —— 应用负载 / 多租户密度 / 长稳
```

执行顺序（Phase）：

| Phase | 内容 | 对应章节 | 本仓库脚本 |
|-------|------|----------|-----------|
| 1 | 环境与基线 | §A 前置 | `00-setup.sh`, `01-baseline-env.sh` |
| 2 | 底层资源 | §A | `02-cpu.sh`, `02-memory.sh`, `02-storage.sh`, `02-network.sh` |
| 3 | 虚拟化开销 | §B | `03-syscall-proc-ipc.sh` |
| 4 | 生命周期 | §C | （编排层埋点，见 §C） |
| 5 | 真实负载 | §D1-D6 | （后续实现） |
| 6 | 规模化 | §D7-D10 | （后续实现） |
| 7 | 汇总报表 | §汇总 | `run-all.sh` 产出 |

---

## A. 底层资源性能

| # | 性能面 | 测什么 | 工具 | 命令模板 | 关键指标 |
|---|--------|--------|------|----------|----------|
| A1 | CPU-整数 | int/分支/逻辑（V8、pnpm、编译） | sysbench / 7z | `sysbench cpu --cpu-max-prime=20000 --threads=1 run` / `7z b` | events/s, MIPS |
| A2 | CPU-浮点 | FP/矩阵（numpy、ML） | stress-ng / numpy | `stress-ng --cpu 1 --cpu-method matrixprod -t 30 --metrics-brief` | bogo-ops/s, GFLOPS |
| A3 | CPU-加密 | AES/SHA/RSA | openssl | `openssl speed -evp aes-256-gcm`, `openssl speed sha256 rsa2048` | MB/s, sign/s |
| A4 | CPU-多核扩展 | 1→N vCPU 加速比 | sysbench | `sysbench cpu --threads=$N run`（扫 1..nproc） | scaling efficiency % |
| A5 | 内存-带宽 | 顺序读写带宽 | STREAM / stress-ng | `stress-ng --stream 1 -t 30 --metrics-brief` | GB/s (copy/scale/add/triad) |
| A6 | 内存-延迟 | cache 分层 + DRAM 延迟曲线 | lmbench | `lat_mem_rd 256M 128` | ns @ L1/L2/L3/DRAM |
| A7 | 内存-随机/TLB | 指针追逐、TLB shootdown | stress-ng / lmbench | `stress-ng --tlb-shootdown 1 -t 30 --metrics-brief` | ops/s |
| A8 | 存储-顺序IO | 大块读写带宽 | fio | `fio --rw=read/write --bs=1M --size=1G --direct=1` | MB/s |
| A9 | 存储-随机IO | 4k 随机 IOPS + 延迟 | fio | `fio --rw=randrw --bs=4k --iodepth=32 --direct=1` | IOPS, P99 lat |
| A10 | 存储-元数据/小文件 | create/stat/unlink、小文件 | fio / tar | `fio --rw=randread --bs=4k --nrfiles=10000` / `time tar xf big.tar` | files/s |
| A11 | 存储-持久化 | fsync/sync 放大 | fio | `fio --rw=write --fsync=1 --bs=4k` | fsync lat, IOPS |
| A12 | 存储-IO延迟 | 单次 IO 延迟分布 | ioping | `ioping -c 30 -D .` | P50/P99 lat |
| A13 | 网络-带宽 | TCP/UDP 吞吐 | iperf3 | `iperf3 -c <host>` / `-u -b 0` | Gbps |
| A14 | 网络-延迟 | 请求-响应往返 | netperf / ping | `netperf -t TCP_RR` / `ping -c 100` | RTT, txn/s |
| A15 | 网络-连接/DNS | 连接建立率、DNS 解析 | wrk / dig | `wrk -c100 -d30s http://...` / `time dig example.com` | conn/s, resolve ms |

---

## B. 系统 / 虚拟化开销（Firecracker 核心代价）

| # | 性能面 | 测什么 | 工具 | 命令模板 | 关键指标 |
|---|--------|--------|------|----------|----------|
| B1 | syscall 延迟 | VM-exit 陷入代价 | lmbench | `lat_syscall null/read/write/stat/open` | ns/call |
| B2 | 进程创建 | fork/exec/shell 延迟 | lmbench / stress-ng | `lat_proc fork/exec/shell`；`stress-ng --fork 1 -t 30` | μs/op, ops/s |
| B3 | 上下文切换 | 不同工作集下切换代价 | lmbench / stress-ng | `lat_ctx -s 0 2 4 8 16`；`stress-ng --switch 1 -t 30` | ns |
| B4 | 线程 | 线程创建/调度 | stress-ng | `stress-ng --pthread 1 -t 30 --metrics-brief` | threads/s |
| B5 | IPC-pipe | 管道吞吐+延迟 | lmbench / stress-ng | `bw_pipe`, `lat_pipe`；`stress-ng --pipe 1 -t 30` | MB/s, lat |
| B6 | IPC-futex/锁 | 锁竞争 | stress-ng | `stress-ng --futex 1 -t 30 --metrics-brief` | ops/s |
| B7 | IPC-epoll/事件 | 事件循环（Node/网关） | stress-ng | `stress-ng --epoll 1 -t 30 --metrics-brief` | events/s |
| B8 | 缺页/mmap | page fault、mmap 延迟 | lmbench / stress-ng | `lat_pagefault`；`stress-ng --mmap 1 -t 30` | μs |
| B9 | 调度公平/抖动 | 多任务时间片公平、唤醒延迟 | schbench / cyclictest | `schbench -m 2 -t 4`；`cyclictest -l 100000` | wakeup lat dist |
| B10 | 综合系统分 | 一键综合 | UnixBench | `./Run -c 1` / `./Run -c $(nproc)` | index score |

---

## C. 沙箱生命周期（E2B 专属，编排层埋点）

> 这些用 §A/§B 工具测不到。在 API/编排器侧埋点，按 **P50/P99/P999** 上报，尾延迟比均值重要。

| # | 性能面 | 测什么 | 关键指标 |
|---|--------|--------|----------|
| C1 | 冷启动全链路 | API 请求 → microVM boot → rootfs/NBD 挂载 → envd ready → 首条命令可执行 | 端到端 P50/P99 |
| C2 | Firecracker boot | kernel boot → init 时间 | ms |
| C3 | 模板加载 | template cache 命中 vs 未命中、从对象存储拉取 | 拉取耗时、命中率 |
| C4 | 快照/恢复 | snapshot resume 时间、恢复后首请求延迟 | resume ms |
| C5 | 冷态 vs 热态首次执行 | page-in 冷态 vs warm | 冷/热延迟差 |
| C6 | 销毁/回收 | teardown、网络/NBD 清理、资源释放 | ms |
| C7 | 启动吞吐 | 单位时间能拉起多少沙箱 | sandboxes/s |

---

## D. 真实负载 & 规模化

| # | 性能面 | 测什么 | 工具/负载 | 关键指标 |
|---|--------|--------|-----------|----------|
| D1 | 包管理 | pnpm/npm install、pip install | 固定 lockfile + `time` | wall time |
| D2 | 构建 | tsc / vite / esbuild / make -j（redis/sqlite） | `time` | wall time |
| D3 | 数据科学 | numpy GEMM、pandas groupby/join、matplotlib 出图 | python 脚本 | wall time |
| D4 | 代码解释链路 | "写代码→装包→跑→出图"全流程 | e2b SDK | 端到端 |
| D5 | 版本控制 | git clone/status/checkout 大仓库 | `time` | wall time |
| D6 | 数据库 | sqlite 事务 / 嵌入式 KV | sqlite-bench | txn/s |
| D7 | 多租户密度 | 单 host N 沙箱并发，单沙箱性能衰减 | 并发拉起 + A2/A5/A9 | per-VM 衰减 % |
| D8 | 噪声邻居干扰 | 一沙箱满载 CPU/IO 时邻居延迟变化 | 加压 + 测量 | 干扰系数 |
| D9 | 长稳/Soak | 持续 N 小时性能漂移、内存泄漏、延迟蠕变 | 循环跑 A/B | drift over time |
| D10 | 资源限制行为 | cgroup CPU/内存配额命中、OOM、IO throttle | 限额下跑 A 类 | 限额下吞吐/行为 |

---

## 方法论（决定数据可信度）

1. **必须有基线对照**：`bare-metal vs Firecracker`（如对标可加 gVisor / 其它云沙箱）。看**虚拟化损耗百分比**，绝对值无意义。
2. **同时报吞吐 + 尾延迟（P99/P999）**：SaaS 体感由尾延迟决定。
3. **冷态 vs 热态分别测**：page cache / template cache 命中差异巨大。
4. **固定变量并记录环境指纹**：vCPU、内存、cgroup、内核/FC 版本、host 负载。
5. **多次取分布**，报 min/median/P99/max，不取单次。
6. **分层定位**：合成基准（A/B）定位"哪层慢"，真实负载（D）作为最终判据。

---

## 结果产出约定

所有脚本将指标以统一格式追加到 `results/<run-id>/metrics.csv`：

```
phase,test_id,name,metric,value,unit,note
```

并保留每个工具的原始输出到 `results/<run-id>/raw/<test_id>.txt`，便于复核。
`01-baseline-env.sh` 产出 `results/<run-id>/env.txt` 作为本次运行的环境指纹。

汇总对照（损耗百分比 / 尾延迟看板）由 `run-all.sh` 在多次运行（如 bare-metal vs sandbox）之间手动 diff `metrics.csv` 完成。
