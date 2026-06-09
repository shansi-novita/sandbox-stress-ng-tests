# E2B Sandbox Performance Runner (Python)

宿主机侧驱动：用 `e2b` Python SDK，**每个测试用例单独创建一个沙箱**执行、互不干扰，
结果按批次归档到 `results/<时间戳>/`。覆盖测试计划 Phase 2–3（A1–A15、B1–B10）。

测试命令取自同目录上层的 in-sandbox bash 脚本（`../02-*.sh` / `../03-*.sh`）；
沙箱模板由 `../Dockerfile` 构建（已预装 stress-ng / fio / sysbench / lmbench / UnixBench / cyclictest / numpy 等）。

## 内存预热（uffd）

Firecracker 用 userfaultfd 懒加载快照内存，guest 页**首次访问**要从快照文件缺页读入，
首访性能远差于稳态。内存类用例 **A5/A6/A7** 因此在**同一个沙箱**内按三段执行：

1. `[cold]`  直接跑基准（含 uffd 冷启动代价）
2. `[warmup]` 预热：分配并逐页写入 ~85% guest RAM，把物理页全部 fault-in
3. `[warm]`  再跑一次基准（稳态）

cold 与 warm 同写一个结果文件，差值即 uffd 冷启动开销。预热比例由 `WARMUP_FRACTION` 控制。

## 布局

```
runner/
  common.py            # 共享：建沙箱 / 跑命令 / 预热命令 / 写结果 / run_case 编排
  requirements.txt     # e2b>=2.15
  cases/case_A1.py ...  case_B10.py   # 25 个用例，每个一个沙箱
  run_all.py           # 总脚本：批次目录 + 逐用例子进程 + 进展记录 + summary
  results/<时间戳>/      # progress.log + <ID>-<name>.txt*25 + summary.txt
```

## 使用

```bash
# 1) 构建模板（在上层 tests/perf 目录，含 Dockerfile）
cd ..
e2b template build --name perf-bench
cd runner

# 2) 安装 SDK 并配置连接（自托管部署）
pip install -r requirements.txt
export E2B_DOMAIN=<你的自托管域名>      # 连 e2b.dev 则不设
export E2B_API_KEY=<your-key>
export PERF_TEMPLATE=perf-bench         # 与 build --name 一致（或用 template id）

# 3) 跑全部 25 个用例
python3 run_all.py

# 只跑部分用例
python3 run_all.py A1 A6 B10

# 单个用例独立运行（会自建批次目录）
DURATION=5 python3 cases/case_A6.py
```

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `E2B_DOMAIN` | _(未设)_ | 自托管部署域名；不设则连 e2b.dev |
| `E2B_API_KEY` | _(必填)_ | SDK 自动读取 |
| `PERF_TEMPLATE` | `perf-bench` | 模板名/ID |
| `DURATION` | `30` | 每个 stress-ng/fio 用例的秒数 |
| `WARMUP_FRACTION` | `0.85` | 预热时 fault-in 的 guest RAM 比例 |
| `STORAGE_DIR` | `/tmp/perf-io` | 沙箱内存储测试写入目录 |
| `IPERF_SERVER` | _(未设)_ | 设了才跑 A13；需对端 `iperf3 -s` |
| `NETPERF_SERVER` | _(未设)_ | 设了才跑 A14；需对端 `netserver` |
| `WRK_TARGET` | _(未设)_ | 设了 A15 才加跑 wrk（dig 始终跑） |

## 结果

- 每个用例一个 `results/<批次>/<ID>-<name>.txt`：含 header（用例/模板/沙箱 id/时间）
  + 每段命令的 `exit`/`elapsed`/stdout/stderr 原始输出。内存类含 `[cold]`/`[warmup]`/`[warm]` 三段。
- `progress.log`：批次与逐用例进展。
- `summary.txt`：每用例一行（状态/耗时/结果文件）。
- 未配 server 的网络用例标记为 `skipped`，不影响整批。

## 宿主机基线模式（Docker 容器，BACKEND=local）

用**完全相同的 25 个用例**测宿主机性能，作为沙箱数据的对比基线（虚拟化损耗%）。
不走 e2b SDK：设 `BACKEND=local` 后，命令在本地用 `subprocess`（`bash -lc`）直接执行；
容器启动即跑全部用例。

```bash
cd ..              # 到 tests/perf（含 Dockerfile / Dockerfile.host / run-host.sh）
bash run-host.sh   # 默认 2 vCPU / 2048 MiB，与沙箱默认一致
```

- **CPU/内存限制**：`--cpuset-cpus`（固定 N 核，`nproc`=N）+ `--memory`/`--memory-swap`（限内存且禁 swap）。
  默认 2C/2048MiB，覆盖：`SANDBOX_CPUS=2 SANDBOX_MEM_MB=1024 DURATION=10 bash run-host.sh`。
- **磁盘**：`-v /tmp:/tmp` 挂载宿主 `/tmp`，存储类用例写 `/tmp/perf-io`（即宿主磁盘）。
- **日志**：落到宿主 `/tmp/result/<时间戳>/`（结构同沙箱模式：`<ID>-<name>.txt` + `progress.log` + `summary.txt`）。
- 宿主无 uffd → 内存类**不做预热**，每条命令跑一次。

与沙箱批次对比：
```bash
diff <(sort /tmp/result/<host批次>/summary.txt) <(sort runner/results/<sandbox批次>/summary.txt)
```

> `BACKEND=local` 也可直接在任意 Linux 机器上裸跑（不经 Docker）：
> `BACKEND=local RESULTS_ROOT=/tmp/result python3 run_all.py`（需本机已装好各 benchmark 工具）。
