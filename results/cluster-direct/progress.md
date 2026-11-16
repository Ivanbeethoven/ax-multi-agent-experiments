# run-002 正式实验阶段结果

截至北京时间 2026-11-16 22:40。每个条件仅取状态表选定且已通过的正式 attempt；原始结果保留在 A366。表中末列相对于 `/srv/ax-cluster/runs/direct/run-002`，该目录下的 `result.json` 是数据来源。

**本轮调度已完成 46/52 项**：C1 已完成，Flat 采用已有结果；C2 12/12，C3 8/11，C4 10/13。

## C1 · fio

### 采用的冷读对照：A366 单机 zero-gap

2026-11-16 19:35–19:38，单机 RustFS 四个数据目录。顺序读 1 GiB、随机读 64 MiB；8 个 fio 作业，BrewFS 使用 8 个 Tokio 线程。吞吐量按应用读取字节除以 fio 子进程实际经过时间计算。8/8 项内容检查通过；本表不计入四节点正式完成数。

| 负载 | 系统 | MiB/s | P99 ms | GET 次数 | 下载字节 B |
|---|---|---|---|---|---|
| 顺序 | JuiceFS Redis（前） | 863.868 | 62.652 | 256 | 1073741824 |
| 顺序 | BrewFS Flat | 806.699 | 35.389 | 256 | 1073742848 |
| 顺序 | BrewFS Workspace Native | 709.795 | 31.064 | 256 | 1073742848 |
| 顺序 | JuiceFS Redis（后） | 811.032 | 70.779 | 256 | 1073741824 |
| 随机 | JuiceFS Redis（前） | 26.048 | 5.603 | 4775 | 1169162240 |
| 随机 | BrewFS Flat | 23.894 | 7.176 | 3950 | 1299056640 |
| 随机 | BrewFS Workspace Native | 22.329 | 7.832 | 4011 | 1303054336 |
| 随机 | JuiceFS Redis（后） | 13.868 | 14.746 | 7839 | 2031435776 |

原始记录：A366 `/srv/ax-cluster/diagnostics/read-prefetch/quick-1g/zero-gap-compare/comparison.json`。

### 四节点正式 fio 已完成条件

表中吞吐使用 fio 自身的读或写带宽。单机候选用例的计时方法及数据见[单独的诊断表](../../bench/cluster/diagnostics/read_path/README.md)。

| 编号 | 系统 | 负载 | Dynamic | B（字节） | 应用耗时 s | fio MiB/s | P99 ms | 原始目录 |
|---|---|---|---|---|---|---|---|---|
| 1 | brewfs-native | sequential/read | on | auto | 3.225 | 747.718 | 108.528 | `01-io/01/r1/attempt-05` |
| 2 | brewfs-native | sequential/write | on | auto | 11.692 | 182.792 | 11.993 | `01-io/02/r1/attempt-04` |
| 3 | brewfs-native | random/read | on | auto | 7.391 | 9.277 | 63.701 | `01-io/03/r1/attempt-05` |
| 4 | brewfs-native | random/write | on | auto | 155.584 | 0.413 | 308.281 | `01-io/04/r1/attempt-04` |
| 5 | juicefs-redis | sequential/read | on | default | 2.508 | 1,011.358 | 103.285 | `01-io/05/r1/attempt-02` |
| 6 | juicefs-redis | sequential/write | on | default | 16.661 | 126.599 | 3.785 | `01-io/06/r1/attempt-02` |
| 7 | juicefs-redis | random/read | on | default | 7.387 | 9.273 | 12.124 | `01-io/07/r1/attempt-02` |
| 8 | juicefs-redis | random/write | on | default | 182.091 | 0.352 | 227.541 | `01-io/08/r1/attempt-02` |
| 9 | brewfs-native | sequential/read | off | default | 4.694 | 487.735 | 219.152 | `01-io/09/r1/attempt-02` |
| 10 | brewfs-native | sequential/write | off | default | 9.094 | 237.974 | 10.027 | `01-io/10/r1/attempt-02` |
| 11 | brewfs-native | random/read | off | default | 16.645 | 3.960 | 31.326 | `01-io/11/r1/attempt-02` |
| 12 | brewfs-native | random/write | off | default | 42.242 | 1.533 | 82.313 | `01-io/12/r1/attempt-02` |
| 13 | brewfs-native | random/read | on | 65536 | 6.720 | 10.261 | 65.274 | `01-io/13/r1/attempt-02` |
| 14 | brewfs-native | random/write | on | 65536 | 158.188 | 0.406 | 333.447 | `01-io/14/r1/attempt-02` |
| 15 | brewfs-native | random/read | on | 4194304 | 6.752 | 10.222 | 60.555 | `01-io/15/r1/attempt-02` |
| 16 | brewfs-native | random/write | on | 4194304 | 160.150 | 0.401 | 354.419 | `01-io/16/r1/attempt-02` |

## C2 · fork 与 Overlay

direct-parent 直接使用父工作区。未单列读写耗时的多子工作区条件保留其批量创建时间。

| 编号 | 系统 | 模式 | 子工作区数 | 创建这批子工作区 s | 读取 s | 写入 s | 原始目录 |
|---|---|---|---|---|---|---|---|
| 1 | brewfs-native | fork | 1 | 0.027 | 71.083 | 23.409 | `02-fork/01/r1/attempt-02` |
| 2 | juicefs-redis | fork | 1 | 4.455 | 63.509 | 15.042 | `02-fork/02/r1/attempt-02` |
| 3 | juicefs-overlay | fork | 1 | 0.076 | 69.071 | 16.475 | `02-fork/03/r1/attempt-01` |
| 4 | brewfs-native | fork | 16 | 0.062 | — | — | `02-fork/04/r1/attempt-01` |
| 5 | juicefs-redis | fork | 16 | 70.669 | — | — | `02-fork/05/r1/attempt-01` |
| 6 | juicefs-overlay | fork | 16 | 1.164 | — | — | `02-fork/06/r1/attempt-01` |
| 7 | brewfs-native | fork | 64 | 0.178 | — | — | `02-fork/07/r1/attempt-01` |
| 8 | juicefs-redis | fork | 64 | 287.680 | — | — | `02-fork/08/r1/attempt-01` |
| 9 | juicefs-overlay | fork | 64 | 4.736 | — | — | `02-fork/09/r1/attempt-01` |
| 10 | brewfs-native | direct-parent | 0 | — | 176.445 | 23.742 | `02-fork/10/r1/attempt-01` |
| 11 | juicefs-redis | direct-parent | 0 | — | 63.011 | 15.340 | `02-fork/11/r1/attempt-01` |
| 12 | juicefs-overlay | direct-parent | 0 | — | 68.438 | 16.646 | `02-fork/12/r1/attempt-01` |

## C3 · 十万小文件

准备、读取分别计时；只有完成的读取条件进入表中。四挂载点功能检查单独记录。

| 编号 | 系统 | 阶段 | Dynamic | 整包缓存 | 阶段耗时 s | 读取字节 B | 原始目录 |
|---|---|---|---|---|---|---|---|
| 1 | brewfs-native-tikv | prepare | on | — | 9,568.590 | — | `03-packed/01/r1/attempt-01` |
| 2 | brewfs-packed | prepare | on | — | 319.666 | — | `03-packed/02/r1/attempt-02` |
| 3 | juicefs-redis | prepare | on | — | 1,031.938 | — | `03-packed/03/r1/attempt-01` |
| 4 | juicefs-tikv | prepare | on | — | 1,056.241 | — | `03-packed/04/r1/attempt-01` |
| 5 | brewfs-packed | prepare | off | — | 335.794 | — | `03-packed/05/r1/attempt-01` |
| 6 | brewfs-native-tikv | read | on | — | 4,353.095 | 26214400000 | `03-packed/06/r1/attempt-02` |
| 8 | juicefs-redis | read | on | — | 423.089 | 26214400000 | `03-packed/08/r1/attempt-02` |
| 9 | juicefs-tikv | read | on | — | 538.815 | 26214400000 | `03-packed/09/r1/attempt-01` |

## C4 · Agent 回放

并发数 1 的一行包含依次执行的 8 个 trace。字节数为回放程序实际读写量，跨机校验在回放计时后进行。

| 编号 | 系统 | 并发数 | Dynamic | 回放耗时 s | 操作次数 | 读取 B | 写入 B | 跨机校验通过数 | 原始目录 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | brewfs-native | 1 | on | 4,473.070 | 11617520 | 2237713780 | 653439576 | 8 | `04-agent-replay/01/r1/attempt-01` |
| 2 | juicefs-redis | 1 | on | 2,349.237 | 11617520 | 2237713780 | 653439576 | 8 | `04-agent-replay/02/r1/attempt-01` |
| 3 | juicefs-overlay | 1 | on | 2,678.977 | 11617520 | 2237713780 | 653439576 | 8 | `04-agent-replay/03/r1/attempt-01` |
| 4 | brewfs-native | 8 | on | 2,582.904 | 11617520 | 2237713780 | 653439576 | 8 | `04-agent-replay/04/r1/attempt-01` |
| 5 | juicefs-redis | 8 | on | 1,191.937 | 11617520 | 2237713780 | 653439576 | 8 | `04-agent-replay/05/r1/attempt-01` |
| 6 | juicefs-overlay | 8 | on | 1,465.726 | 11617520 | 2237713780 | 653439576 | 8 | `04-agent-replay/06/r1/attempt-01` |
| 7 | brewfs-native | 16 | on | 2,746.062 | 23235040 | 4475427560 | 1306879152 | 16 | `04-agent-replay/07/r1/attempt-02` |
| 8 | juicefs-redis | 16 | on | 1,385.636 | 23235040 | 4475427560 | 1306879152 | 16 | `04-agent-replay/08/r1/attempt-01` |
| 9 | juicefs-overlay | 16 | on | 1,618.934 | 23235040 | 4475427560 | 1306879152 | 16 | `04-agent-replay/09/r1/attempt-01` |
| 10 | brewfs-native | 64 | on | 4,914.838 | 92940160 | 17901710240 | 5227516608 | 64 | `04-agent-replay/10/r1/attempt-02` |
