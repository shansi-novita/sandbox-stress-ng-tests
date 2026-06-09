# E2B Sandbox 性能测试 Runner

同一套测试用例(`cases/case_*.py`),两种执行后端:

- **沙箱模式 (`BACKEND=e2b`,默认)**:用 `e2b` Python SDK,**每个用例单独创建一个沙箱**执行、互不干扰,结果按批次归档到 `results/<时间戳>/`。
- **主机基线模式 (`BACKEND=local`)**:不走 SDK,命令用 `subprocess`(`bash -lc`)直接执行,通常跑在一个 **CPU/内存限额与沙箱默认一致(2 vCPU / 2048 MiB)** 的 Docker 容器里,作为"虚拟化损耗%"的对比基线。

用例与编排两种模式**完全共用**,结果文件格式一致,天然可对比。

## 用例清单

- `A1`–`A16`:底层资源(CPU / 内存 / 存储 / 网络)
- `B1`–`B19`:虚拟化开销(syscall / 进程 / 上下文切换 / IPC / 缺页 / 调度 / 综合)

内存类用例(A5/A6/A7)在沙箱模式下走 **cold → warmup → warm** 三段(见下),其余用例跑一次或按 `repeats` 重复。

---

## 内存预热(uffd)

Firecracker 用 userfaultfd 懒加载快照内存,guest 页**首次访问**要从快照文件缺页读入,首访性能远差于稳态。内存类用例因此在**同一个沙箱**内按三段执行:

1. `[cold]`  直接跑基准(含 uffd 冷启动代价)
2. `[warmup]` 预热:分配并逐页写入 ~85% guest RAM,把物理页全部 fault-in
3. `[warm]`  再跑一次基准(稳态)

cold 与 warm 同写一个结果文件,差值即 uffd 冷启动开销。预热比例由 `WARMUP_FRACTION` 控制。主机模式无 uffd → 不预热,每条命令跑一次。

---

## 环境调优 Hook(SANDBOX_HOOK)

设置 `SANDBOX_HOOK` 后,**每个用例的沙箱启动后、跑测试命令前**,会先以 **root** 执行一次该 shell 命令,用于调优文件系统等内核参数。命令结果记录为结果文件里的 `[hook]` 段,其 exit code 计入用例状态(调优失败会被标记出来)。

> 因为每个用例都是全新沙箱,hook 对每个沙箱各跑一次——正好保证调优应用到每一次测量。沙箱模式下 hook 用 `user="root"` 执行;主机模式(容器内已是 root)直接执行。`SANDBOX_HOOK` 的内容**不做占位符替换**,可放心使用 `$DEV` / `$(...)`。

```bash
# 例 1:remount noatime 后再测存储
SANDBOX_HOOK='mount -o remount,noatime /' \
  python3 run_all.py A8 A9 A12

# 例 2:调 ext4 inode_readahead_blks(2 的幂),再测
SANDBOX_HOOK='DEV=$(basename $(findmnt -no SOURCE /)); echo 64 > /sys/fs/ext4/$DEV/inode_readahead_blks' \
  python3 run_all.py A8 A9 A12

# 例 3:两项一起
SANDBOX_HOOK='mount -o remount,noatime /; DEV=$(basename $(findmnt -no SOURCE /)); echo 64 > /sys/fs/ext4/$DEV/inode_readahead_blks' \
  python3 run_all.py A8 A9

# 主机/容器模式同理(经 docker run -e 传入)
docker run --rm --cpuset-cpus=0-1 --memory=2048m --memory-swap=2048m \
  -v /tmp:/tmp -v /home/shansi/log:/home/shansi/log -e RESULTS_ROOT=/home/shansi/log \
  -e SANDBOX_HOOK='mount -o remount,noatime /' \
  perf-bench-host python3 /test/runner/run_all.py A8 A9
```

做 A/B 对比:同一批用例分别 **不设** 和 **设** `SANDBOX_HOOK` 各跑一次,对比两个批次的 `summary.txt` / 各 `A8/A9/A12` 指标即可量化该调优的收益。结果文件 `[hook]` 段可确认调优是否真的生效(`exit=0` + 校验输出)。

---

## perf 性能/热点分析(PERF_TRACE)

设 `PERF_TRACE=1` 后,**匹配 `PERF_MATCH`(默认 `stress-ng`)的命令会在 `perf` 下跑**,产出写进该用例结果文件:

- `=== perf stat ===`:计数器摘要(task-clock、context-switches、cpu-migrations、page-faults;IPC/cache 等**硬件计数器**在无 vPMU 时显示 `<not supported>`)。
- `=== perf report (hotspots) ===`:`perf record` 采样得到的**符号级热点 top 表**。

**沙箱与主机两种模式都支持**,用法与平时一致,只多加 `PERF_TRACE=1`:

```bash
# 沙箱模式(perf 跑在 guest 内)
PERF_TRACE=1 DURATION=10 python3 cases/case_B6.py
PERF_TRACE=1 python3 run_all.py B6 B7 A2

# 主机模式(perf 跑在容器内;run-host.sh 会自动加 --cap-add SYS_ADMIN,PERFMON)
PERF_TRACE=1 DURATION=10 bash ../run-host.sh

# 主机模式直接 docker run(需手动加 caps + 透传 PERF_TRACE)
docker run --rm --cpuset-cpus=0-1 --memory=2048m --memory-swap=2048m \
  --cap-add SYS_ADMIN --cap-add PERFMON \
  -v /tmp:/tmp -v /home/shansi/log:/home/shansi/log -e RESULTS_ROOT=/home/shansi/log \
  -e PERF_TRACE=1 -e DURATION=10 \
  perf-bench-host python3 /test/runner/run_all.py B6
```

可调:`PERF_MODE=stat`(只要计数器,开销最小)/ `PERF_MODE=record`(只要热点);`PERF_MATCH` 改匹配范围;`PERF_EVENT` 换采样事件。

**三点注意:**

1. **无硬件 PMU**:Firecracker guest 和多数云 L1 VM 不暴露 vPMU,所以采样默认用软件事件 `cpu-clock`(已能出热点);`perf stat` 的 `cycles`/`instructions`/`cache-misses` 等会是 `<not supported>`——这本身就是"无 vPMU"的证据。
2. **符号**:发行版 `stress-ng` 被 strip,用户态热点多显示 `[unknown]`;**内核侧符号可解析**(perf 以 root 跑并放宽 `kptr_restrict`),而 stress-ng 的开销大头本就在内核(syscall/陷出),所以内核热点正是关注点。
3. **计时被污染 / 跑两趟 / root**:开关打开时,被包裹用例的 `[run]` 段是 **perf 下的运行**,其 `time`/health 计时被采样开销扰动(已替换掉干净计时);`both` 模式一条命令会跑两趟(stat + record),`repeats>1` 会成倍放大耗时;沙箱模式下该命令**以 root 执行**(绕过 `perf_event_paranoid`)。需要干净基线时,另跑一批**不设** `PERF_TRACE` 的即可。

> 这与 `tests/perf/hostobs/observe.py` 的 `perf kvm stat`(在 L1 观测 Firecracker 进程的 VM-exit)是互补的两层:这里看的是 **workload(stress-ng)自身**的微架构与热点,hostobs 看的是**虚拟化陷出**。

---

## 布局

```
tests/perf/
  Dockerfile           # 基础镜像 perf-bench(全部 benchmark 工具 + python3/numpy/lmbench/UnixBench)
  Dockerfile.host      # FROM perf-bench,容器启动即跑全部用例(BACKEND=local)
  run-host.sh          # 主机基线启动器:建镜像 + 限额 docker run
  runner/
    common.py          # 共享:建沙箱 / 跑命令 / 预热 / 写结果 / run_case 编排
    requirements.txt   # e2b>=2.15(仅沙箱模式需要)
    cases/case_*.py    # 用例,每个一个文件
    run_all.py         # 总脚本:批次目录 + 逐用例子进程 + 进展 + summary
    results/<时间戳>/   # progress.log + <ID>-<name>.txt + summary.txt
```

---

## 一、前置操作

### 沙箱模式前置
```bash
# 1) 构建模板(在 tests/perf 目录,含 Dockerfile)
cd tests/perf
e2b template build --name perf-bench

# 2) 安装 SDK 并配置连接(自托管部署)
cd runner
pip install -r requirements.txt
export E2B_DOMAIN=<你的自托管域名>      # 连 e2b.dev 则不设
export E2B_API_KEY=<your-key>
export PERF_TEMPLATE=perf-bench         # 与 build --name 一致(或用 template id)
```

### 主机模式前置
```bash
# 构建镜像(基础镜像 + host 镜像)。run-host.sh 会自动构建;手动构建:
cd tests/perf
docker build -t perf-bench      -f Dockerfile      .
docker build -t perf-bench-host -f Dockerfile.host .
```

---

## 二、沙箱模式(BACKEND=e2b)

> 在 `tests/perf/runner/` 目录执行。结果落在 `runner/results/<时间戳>/`。

```bash
cd tests/perf/runner

# 全部用例
python3 run_all.py

# 多个用例
python3 run_all.py B11 B12 B13 B14 B15 B16 B17

# 单个用例(独立运行,会自建批次目录)
python3 cases/case_A6.py

# 缩短时长冒烟
DURATION=5 python3 cases/case_A1.py
```

后台运行(SSH 断了也不停):
```bash
mkdir -p /home/shansi/log
nohup env RESULTS_ROOT=/home/shansi/log \
  python3 run_all.py B11 B12 B13 B14 B15 B16 B17 \
  > /home/shansi/log/run-sandbox.console.log 2>&1 &
tail -f /home/shansi/log/run-sandbox.console.log
```

---

## 三、主机基线模式(BACKEND=local,Docker 容器)

> 用**完全相同的用例**测宿主机性能。容器启动即跑(`CMD` = `run_all.py`)。
> CPU/内存限额默认 2 vCPU / 2048 MiB,与沙箱默认一致。

### 3.1 一键脚本(跑全部用例)
```bash
cd tests/perf
bash run-host.sh
# 自定义限额 / 时长:
SANDBOX_CPUS=2 SANDBOX_MEM_MB=1024 DURATION=10 bash run-host.sh
```
结果落在宿主 `/tmp/result/<时间戳>/`(脚本内 `-v /tmp:/tmp`)。

### 3.2 直接 docker run(可选用例 + 自定义日志目录 + 后台)

镜像 `CMD` 默认跑全部用例;在镜像名后**追加完整命令**即可覆盖,只跑指定用例。
日志目录用 `-v` 挂入 + `-e RESULTS_ROOT` 指过去。

**全部用例:**
```bash
docker run --rm --name perf-host \
  --cpuset-cpus=0-1 --memory=2048m --memory-swap=2048m \
  -v /tmp:/tmp \
  perf-bench-host
```

**多个用例(B11~B17),日志到 `/home/shansi/log`,后台运行:**
```bash
mkdir -p /home/shansi/log
nohup docker run --rm --name perf-b11-17 \
  --cpuset-cpus=0-1 --memory=2048m --memory-swap=2048m \
  -v /tmp:/tmp \
  -v /home/shansi/log:/home/shansi/log \
  -e RESULTS_ROOT=/home/shansi/log \
  perf-bench-host \
  python3 /test/runner/run_all.py B11 B12 B13 B14 B15 B16 B17 \
  > /home/shansi/log/run-b11-17.console.log 2>&1 &
```

**单个用例(B8):**
```bash
docker run --rm --name perf-b8 \
  --cpuset-cpus=0-1 --memory=2048m --memory-swap=2048m \
  -v /tmp:/tmp -v /home/shansi/log:/home/shansi/log \
  -e RESULTS_ROOT=/home/shansi/log -e DURATION=10 \
  perf-bench-host \
  python3 /test/runner/run_all.py B8
```

参数说明:
- `--cpuset-cpus=0-1` 固定到 2 个核(`nproc`=2,匹配 2-vCPU 沙箱;`--cpus` quota 不改 `nproc`,会让多核/扩展性用例失真)。
- `--memory` + 等值 `--memory-swap` = 限内存且禁 swap。
- `-v /tmp:/tmp` 让容器用宿主磁盘(存储类用例写 `/tmp/perf-io`)。
- `-e RESULTS_ROOT=<dir>` + 同名 `-v` 挂载 = 结果与日志落到宿主指定目录。
- 末尾 `nohup … &` = 后台运行。

查看 / 停止:
```bash
tail -f /home/shansi/log/run-b11-17.console.log   # 实时 console
tail -f /home/shansi/log/*/progress.log           # 进度
docker ps --filter name=perf-b11-17               # 状态
docker stop perf-b11-17                            # 停止
```

> 也可在任意已装好 benchmark 工具的 Linux 机器上**裸跑**(不经 Docker):
> `BACKEND=local RESULTS_ROOT=/tmp/result python3 run_all.py`。
> ⚠️ 裸跑在生产节点会与真实负载抢资源、且无内存上限,**建议优先用容器**(有 cpuset+内存限额),必要时放进 `tmux`/`nohup` 避免断线中断。

---

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `BACKEND` | `e2b` | `e2b`=建沙箱+SDK;`local`=本地 subprocess(主机基线) |
| `E2B_DOMAIN` | _(未设)_ | 自托管部署域名;不设则连 e2b.dev(仅沙箱模式) |
| `E2B_API_KEY` | _(必填)_ | SDK 自动读取(仅沙箱模式) |
| `PERF_TEMPLATE` | `perf-bench` | 模板名/ID(仅沙箱模式) |
| `DURATION` | `30` | 每个 stress-ng/fio 用例的秒数 |
| `WARMUP_FRACTION` | `0.85` | 预热时 fault-in 的 guest RAM 比例(仅沙箱内存类) |
| `STORAGE_DIR` | `/tmp/perf-io` | 存储测试写入目录 |
| `RESULTS_ROOT` | `runner/results` | 批次目录根(主机镜像内默认 `/tmp/result`) |
| `BATCH_DIR` | _(自动)_ | 批次目录;`run_all.py` 注入,单用例独立运行时自建 |
| `STREAM` | `1` | 实时流式输出到终端;`0` 静默 |
| `SANDBOX_HOOK` | _(未设)_ | 每个沙箱启动后、跑用例前,**以 root 执行一次**的 shell 命令(环境调优,见下) |
| `PERF_TRACE` | `0` | 设 `1` 时用 **perf 跟踪 stress-ng**(perf stat + 热点),见「perf 性能/热点分析」 |
| `PERF_MATCH` | `stress-ng` | 决定哪些命令被 perf 包裹的正则 |
| `PERF_MODE` | `both` | `both` / `stat` / `record` |
| `PERF_EVENT` | `cpu-clock` | record 采样事件(软件事件,无 vPMU 也能出热点) |
| `PERF_FREQ` / `PERF_REPORT_LINES` | `999` / `40` | 采样频率 / 热点表行数上限 |
| `SANDBOX_CPUS` / `SANDBOX_MEM_MB` | `2` / `2048` | `run-host.sh` 的容器限额 |
| `IPERF_SERVER` / `NETPERF_SERVER` / `WRK_TARGET` | _(未设)_ | 设了才跑对应网络用例(A13/A14/A15) |

---

## 结果

- 每个用例一个 `<批次>/<ID>-<name>.txt`:header(用例/模板/沙箱 id/状态/时间) + 每段命令的 `exit`/`elapsed`/stdout/stderr。内存类含 `[cold]`/`[warmup]`/`[warm]` 三段。
- `progress.log`:批次与逐用例进展。
- `summary.txt`:每用例一行(耗时/结果文件/状态)。
- 未配 server 的网络用例标记为 `skipped`,不影响整批。

### 主机 vs 沙箱对比
```bash
diff <(sort /tmp/result/<host批次>/summary.txt) \
     <(sort runner/results/<sandbox批次>/summary.txt)
```
