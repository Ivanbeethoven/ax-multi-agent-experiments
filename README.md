# BrewFS 多 Agent 存储实验

本仓库比较 BrewFS、JuiceFS 和 JuiceFS Overlay 的文件 I/O、多 Agent 工作区与只读小文件共享。使用完整文件系统 trace 回放，不调用模型。

**新一轮 C1～C4 使用六机 eth0 直连。** 从 [直连实验交接](docs/HANDOFF-DIRECT-NETWORK.md) 开始，网络地址见 [NETWORK](docs/NETWORK.md)，范围和结果位置见 [campaign-direct.json](configs/campaign-direct.json)。分组操作细节：[C1～C3](docs/HANDOFF-C1-C3.md) · [C4](docs/HANDOFF-C4.md)。

| 实验 | 要回答的问题 | 计划 |
|---|---|---|
| C1 文件 I/O 与动态块 | 不同应用读写尺寸与对象 B/q 如何影响延迟、吞吐和实际字节？ | [设计与参数](experiments/c1-io/README.md) |
| C2 多 Agent | 共享父数据与私有名字空间在并发完整任务中节省多少时间和资源？ | [设计与参数](experiments/c2-agents/README.md) |
| C3 只读小文件 | Packed 联合页能否加速数据加载，准备成本需要多少次复用才能收回？ | [设计与参数](experiments/c3-ml/README.md) |
| C4 fork 与合并 | 私有目录/范围增量能否降低派生、修改与归并成本？ | [设计与参数](experiments/c4-fork/README.md) |

技术消融及配对见 [ABLATIONS](docs/ABLATIONS.md)。普通任务采用 Native Overlay；Packed 用于只读、反复使用的大量小文件。各工作区的可写数据独立，同机只读缓存按主机总预算比较共享与独立两种方式。

| 要找什么 | 入口 |
|---|---|
| 机器、软件来源、四节点 RustFS、Redis、准备命令 | [SETUP](docs/SETUP.md) |
| 每个挂载的缓存、预读、上传和资源限制 | [MOUNTS](docs/MOUNTS.md) · [配置](configs/mount-profiles.json) |
| 输入下载位置 | [trace-inputs.json](configs/trace-inputs.json) |
| 指标采集与结果保存目录 | [METRICS](docs/METRICS.md) · [本轮结果](results/cluster-direct/README.md) |
| 范围、条件与重复次数 | [campaign-direct.json](configs/campaign-direct.json) · [实验索引](experiments/README.md) |
| 历史计划、运行结果和诊断材料 | [archive](archive/README.md) |

目录按用途组织：`experiments/` 为四个 C；`configs/` 为参数；`bench/` 为实验工具；`infra/` 为环境准备；`tools/` 为 Rust 工具；`datasets/` 为输入工具；`docs/` 为共用说明；`results/` 为本地结果；`archive/` 为历史材料。

```powershell
python -m unittest discover -s tests/cluster -v
python -m bench.cluster.plan --group C1 --out runs/c1-plan.json
```

计划生成器只输出任务。执行者按交接文档手动准备与运行，将缺少的适配补齐并记录状态。直连轮次 C1～C4 为 137 个条件、735 次系统运行，包含三次重复；最高并发 128。C4 最多 64 个 child 加 base/target。使用现有六机，CephFS 保留后续扩展位置。
