# hostobs — 嵌套虚拟化性能分析

沙箱(**L2** = Firecracker microVM)跑在一台云 **VM(L1)** 上,L1 本身又是云厂商物理机(**L0**)的 guest。因此 stress-ng 等基准运行在**嵌套虚拟化**下:L2 每次 VM-exit 先由 L1 的 KVM 处理,部分还要 L1 再 exit 到 L0,产生放大。

这组工具量化并定位这部分开销。**面向标准 Ubuntu(KVM)的 orchestrator 节点设计**。

## 前提与局限(先读)

- **只有这台嵌套 VM、无裸金属** → 无法跑"非嵌套 Firecracker"参照,**无法把"嵌套惩罚"从"Firecracker 本身开销"里干净剥离**。产出是:沙箱在 VM 里的**总开销**(L2/L1 差分)+ **VM-exit 硬证据** + 与公开裸机数据对照的**嵌套增量估计**。
- `perf kvm stat`(在 L1)只能看到 **L2→L1** 的 exit;L1→L0 的二次放大需 L0 访问,拿不到,只能由 **L1 steal 升高 / FC 宿主 CPU 膨胀**间接佐证。

## 三个文件

| 文件 | 跑在哪 | 作用 |
|------|--------|------|
| `characterize.sh` | **L1**,root,一次性 | 固化嵌套配置:vmx/svm、kvm `nested/ept/apicv`、/dev/kvm、clocksource、L1 steal 基线、perf/debugfs 可用性 |
| `observe.py` | **L1**,root,常驻 | 与测试批次并发:发现 Firecracker 进程→按 sandbox id 归因→生命周期内采 VM-exit 计数(KVM debugfs per-VM)、FC 宿主 CPU(vCPU vs 其它线程)、ctxt、L1 steal、cgroup;`HOSTOBS_PERF=1` 加 perf kvm stat 取 exit 原因直方图 |
| `analyze.py` | 任意,后处理 | 读 L2(e2b)/ L1(local)两批结果算逐用例 loss%,按 sandbox id join `observe.py` 输出,出 loss 表 / 指纹表 / 嵌套估计表 → `report.md` |

**关联键 = sandbox id**:Firecracker 命令行的 `--api-sock .../fc-<sandboxID>-<rnd>.sock` ↔ runner 结果头的 `# sandbox <id>`。

## 测量协议

> ⚠️ **必须在安静 / 专用的 orchestrator 上跑**。同机其它用户的沙箱会造成 CPU steal,同时污染 L2 测量(沙箱内 steal%)和 L1 观测。这既是测量卫生,也让 `observe.py` 能直接观测到的所有 FC 进程就是本批测试的沙箱。

### 步骤

```bash
# 0) L1 上 root,先存档环境画像
sudo bash tests/perf/hostobs/characterize.sh /tmp/L1-characterize.txt

# 1) L1 上 root,启动观测器(批次开始前先起);深采加 HOSTOBS_PERF=1
sudo HOSTOBS_PERF=1 HOSTOBS_DIR=/tmp/hostobs-$(date +%s) \
     python3 tests/perf/hostobs/observe.py
#    它会忽略启动时已存在的沙箱,只采测试期间新出现的;批次跑完后 Ctrl-C

# 2) 能访问部署的机器上,跑 L2(沙箱)批次(沿用现状:绑核 PIN_CORE=1)
cd tests/perf/runner
E2B_DOMAIN=<域名> E2B_API_KEY=<key> PERF_TEMPLATE=perf-bench \
  python3 run_all.py            # 或挑子集: python3 run_all.py A1 A2 B1 B3 B7 B8 A8

# 3) 出 L1 host 基线(Docker 在 L1,2vCPU/2048MiB,用例完全相同)
cd tests/perf && bash run-host.sh

# 4) 合成报告(把 hostobs 目录传进来获得 VM-exit 列)
python3 tests/perf/hostobs/analyze.py \
  --l2 tests/perf/runner/results/<e2b批次> \
  --l1 /tmp/result/<local批次> \
  --hostobs /tmp/hostobs-<...> \
  --out /tmp/nested-report.md
```

建议 `DURATION` 默认 30s、`repeats≥3`(降低单次方差,尤其 exit-sensitive / 存储类);沙箱内 health 已对 steal>3% 的 run 标 `SUSPECT`,据此剔除低可信 run。

## 怎么读报告

- **表 2(指纹)**是核心:`cpu` 组(A1/A2/A3 纯用户态、几乎不陷出)loss% 应≈0,作为"无虚拟化代价"基准;`exit`/`io`/`fs` 组 loss% 显著、且**表 1 对应高 VM-exit 次数 / 高 FC 宿主 CPU** → 开销由 VM-exit 处理主导,正是嵌套放大的部位。
- **表 3(估计)**:exit/io/fs 组 loss% 减去公开裸机 Firecracker 参考带 = 嵌套增量**估计**(非测量,基准带是粗略公开值)。
- `observe.py` 的 `perf_kvm.reasons` 看 exit 原因构成:`EPT_VIOLATION`(内存)/`EXTERNAL_INTERRUPT`(中断)/`IO_INSTRUCTION`(I/O)占比高 = 嵌套代价集中处;`l1_steal_pct` 升高 = L0 在二次抢占 L1。

## 依赖

- L1:`perf`(可选,深采用;`apt install linux-tools-$(uname -r)`)、KVM debugfs(标准 Ubuntu 默认挂在 `/sys/kernel/debug/kvm/`,运行中 VM 会出现 `<pid>-<fd>/` 子目录)、root。
- `observe.py` / `analyze.py` 仅用 Python 标准库,无需第三方包。
