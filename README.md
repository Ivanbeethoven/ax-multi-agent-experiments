# BrewFS 存储实验

比较 BrewFS 与 JuiceFS 的基础读写、fork、Packed 小文件读取和多 Agent 文件系统 trace 回放。

**四组方案已确认，执行配置见 `configs/experiment-suite.json`。** 先看 [四组实验方案](docs/PLAN-REVIEW.md)。

| 实验 | 内容 |
|---|---|
| [1. 基础 fio](experiments/01-io.md) | 原版独立卷、Workspace、JuiceFS 的顺序和随机读写，附带块尺寸与 Dynamic 比较 |
| [2. fork 与 Overlay](experiments/02-fork.md) | 子工作区创建时间与增加一层后的读写开销 |
| [3. Packed](experiments/03-packed.md) | 10 万小文件，Native、Packed、JuiceFS Redis/TiKV |
| [4. 多 Agent 回放](experiments/04-agent-replay.md) | 1/8/16/64 个 Agent，固定种子的随机等待 |

运行：[准备、启动与单次检查](docs/RUNNING.md)。本轮每个条件一次，共 **56 项**。

环境：[服务器与依赖](docs/SETUP.md) · [内网](docs/NETWORK.md) · [挂载参数](docs/MOUNTS.md) · [指标](docs/METRICS.md)。

输入：[trace 下载目录](configs/trace-inputs.json)。已有 trace 使用 `tools/full-fuse-replay` 回放。

| 目录 | 用途 |
|---|---|
| `experiments/` | 四组实验说明 |
| `configs/` | 主机、网络、缓存和负载参数 |
| `bench/cluster/` | 挂载、运行、采集、状态与汇总 |
| `infra/idcai/`、`infra/rustfs/` | 服务器准备、构建分发与存储服务部署 |
| `tools/` | Rust 回放、文件读写、导入与工作区工具 |
| `datasets/`、`inputs/`、`scripts/`、`skills/` | 负载目录、数据采集与回放工具 |
| `tests/` | 当前代码的检查 |
| `results/` | 当前实验结果 |
| `archive/` | 历史结果、规划和对应代码 |

原始结果按运行编号保存，实验完成后取回本地。历史结果见 [archive/results](archive/results/)。
