# 云上多 Agent 的存储实验

本仓库独立保存实验规划、测试脚本、固定输入和阿里云部署文件。以下命令都在仓库根目录运行；先用 `python -m pip install -r scripts/requirements.txt` 安装检查依赖。正式执行文件系统操作需要 Linux。

旧核查记录保留了拆分前的路径和文件摘要。重新采集代码记录时，`scripts/capture_sources.py` 默认在本仓库的同级目录寻找 `brewfs-upstream` 与 `ax-integration-upstream`；也可用环境变量 `AX_EXPERIMENT_SOURCE_ROOT` 指定它们的父目录。它不是日常测试的前置步骤。

**先看 [实验怎么做](execution-plan.zh.md)。**这是当前的执行计划，按“测什么、和谁比、怎么跑、记录什么、怎样判断”展开。

2026-10-24 增加 [只有容器使用权的 Kubernetes 方案](scenarios/k8s-containers/README.md)：适用于只能进入已分配容器、不能创建 Pod/Service 的环境。本轮仅以 JuiceFS 为外部对照，包含[部署与下载](scenarios/k8s-containers/deployment.md)、[指标采集](scenarios/k8s-containers/observability.md)和[4—128客户端规模方案](scenarios/k8s-containers/scale.md)；原 ECS 方案保留。

我们要回答四个问题：

| 问题 | 对应测试 |
|---|---|
| 同样的机器，能同时维护多少工作区？ | T1 创建、读写、保存和持续请求 |
| 分叉、保留版本、共享资料要花多少时间和空间？ | T2 版本；T3 生产与共享读取 |
| 存储变快后，工具和真实 Agent 的任务是否也更快？ | T4 固定工具回放；少量真实运行 |
| 只迁走选中的 Agent，要停多久，会影响多少邻居？ | T5 正常迁移、未保存修改、故障 |

A1–A6 分别改变共享方式、元数据、增量保存和读取布局，用来解释差异来自哪项设计。对比方案固定为 BrewFS、XFS reflink＋restic、JuiceFS、CephFS，以及云盘＋XFS 换挂载；具体配置见 [对照方案](baselines.md)。

## 现在做到哪里

- AX 已能运行多 Agent，这是已有能力。
- 已准备确定性文件操作、输入检查、结果汇总、阿里云 Terraform 和部分基线接口。
- Rust 文件操作回放器在独立仓库 [fuse-workload-replay](https://github.com/Ivanbeethoven/fuse-workload-replay)，已完成本地暖缓存测试。
- 16 个真实任务的题目、代码版本和评分材料已固定；还没有采集合格的工具轨迹。
- 尚未部署这套云端对比环境，也没有正式性能结果。多节点执行、真实会话恢复和故障操作仍需接通。
- BrewFS 接口按目前安排暂缓，先把其他方案跑通。

已完成一次 [Windows → WSL 的 OpenCode 小任务恢复测试](validation/opencode-resume-20261023/README.md)：空 HOME 下，原生导出/导入并告知新路径后续跑成功；直接复制数据库失败。它不是云端或 BrewFS 迁移结果。

四道较难 SWE-bench Verified 题的本机解题记录见 [结果与 trace](results/swebench-verified-20261023.md)，运行和导出方法见 [脚本说明](scripts/swebench-local.zh.md)。这组测试独立于 16 题计划，记录的是 Agent 会话和工具调用，尚不是 POSIX/FUSE 回放轨迹。

先完成了 [Pytest 固定修复步骤的文件采集与 Rust 回放](results/fs-trace-pytest-10356-20261023/README.md)，随后已完成 [一次完整 OpenCode 任务的挂载层采集与 Rust 回放](results/opencode-full-fuse-pytest-10356-20261023/README.md)：覆盖任务整个运行期间的仓库和全部 HOME/XDG 状态，共 174.7 万个回调，最终文件树与会话数据库核对通过。旧模型入口仍默认阻止无文件记录器的开跑，新入口见 [完整任务说明](scripts/full-fuse-trace.zh.md)。

## 从哪里开始

先在阿里云部署 B1/B2，做 12 批保存与恢复检查；再用已选的两题验证记录和回放过程，最后采完 16 题。不要一开始就跑全部规模或花大量模型 token。

生成、执行前检查和汇总命令见 [脚本使用说明](qualification.md)。下面两条只做本地检查：

```text
python scripts/check_plan.py
python -m unittest discover -s tests -v
```

需要细节时再看：

- [输入和测试数量](workloads.json)、[实验清单](matrix.csv)、[计时和失败处理](metrics.md)。
- [固定回放与真实 Agent 各测什么](replay-experiments.zh.md)、[会话怎样随工作区移动](agent-trace-and-resume.zh.md)、[迁移步骤](integration-and-migration.md)。
- [阿里云部署文件](infra/alicloud/README.md)、[16 个任务与评分材料](inputs/README.md)。

旧版 [plan.zh.md](plan.zh.md) 和 [规划 PDF](report/main.pdf) 留作讨论记录，重复数、模型预算和开跑步骤以当前执行计划为准。`sources.json` 也只是旧代码的核查记录，不能用它否定用户已经完成的 AX 集成。
