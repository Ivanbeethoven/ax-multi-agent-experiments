# BrewFS 多 Agent 存储实验

研究云上多 Agent 的工作区如何共享数据、独立修改、保存版本，以及在容器之间交接状态。本仓库保存实验方案、文件系统负载、采集与回放工具、部署和指标收集脚本。

当前对比 **BrewFS 与 JuiceFS**，共用 **4节点 RustFS**，自行部署 Redis。BrewFS 使用执行开始时 [官方仓库](https://github.com/brewfs/brewfs) 默认分支的最新提交，记录完整 commit，并固定到实验结束。

## 从哪里开始

| 你要做什么 | 入口 |
|---|---|
| 让一个 Agent 接手多容器测试 | [执行指令](scenarios/k8s-containers/execute-agent.prompt.md) |
| 找到7份负载、下载和校验 | [数据目录与命令](scenarios/k8s-containers/data-catalog.md) · [完整路径与SHA256](scenarios/k8s-containers/archives.json) |
| 重新采集真实 Agent 的文件访问 | [Trace采集 Skill](skills/agent-fs-trace/SKILL.md) · [环境与完整命令](skills/agent-fs-trace/references/commands.md) |
| 部署存储和监控 | [服务部署](scenarios/k8s-containers/deployment.md) · [指标采集](scenarios/k8s-containers/observability.md) |
| 查看实验规模和每组测什么 | [测试清单](scenarios/k8s-containers/README.md) · [规模方案](scenarios/k8s-containers/scale.md) |

当前执行范围以[执行指令](scenarios/k8s-containers/execute-agent.prompt.md)为准。较早的部署文档保留了单节点 RustFS 的起步示例，正式测试使用4节点配置。

## 已有负载

已整理两批共 **7份文件系统记录**。Pytest是同一道题的两次独立运行；Rust构建和归档处理是新增任务类型。

| case ID | 内容 | FUSE回调数 |
|---|---|---:|
| `django` | Django代码任务 | 57,787,980 |
| `sklearn` | scikit-learn代码任务 | 9,233,326 |
| `sympy` | SymPy代码任务 | 7,265,338 |
| `pytest-full-02` | 首批Pytest任务 | 1,747,109 |
| `pytest-20261026` | 新一轮Pytest任务 | 2,836,142 |
| `ripgrep-20261026` | ripgrep构建与测试 | 3,122,195 |
| `archive-io-20261026` | 小文件/大文件归档、解包、扫描与SQLite索引 | 3,673,814 |

完整归档保存在杭州OSS，压缩包合计约 **9.69 GiB**。仓库可公开克隆；**OSS桶仍为私有**，下载需要只读身份或单独提供的临时签名链接。

```text
oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023/
oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023/deepseek-opencode-20261026/
```

每份数据包含冻结的初始目录、`capture/events.jsonl`、按SHA256保存的写入内容，以及最终状态和验收记录。详细URI、HTTPS地址、大小、SHA256和解压目录见[统一清单](scenarios/k8s-containers/archives.json)。

## 先验证一份回放

在Linux上运行。需要Python 3.10+、Rust/Cargo（实测1.98.1）、已配置OSS读取权限的Aliyun CLI，以及trace中实际文件操作所需的权限。使用新的输出目录。

```bash
set -euo pipefail
git clone https://github.com/Ivanbeethoven/ax-multi-agent-experiments.git
cd ax-multi-agent-experiments
export K="$PWD/scenarios/k8s-containers"
export RUN_ROOT="$HOME/ax-replay-demo"

cargo build --release --locked \
  --manifest-path tools/full-fuse-replay/Cargo.toml \
  --target-dir /tmp/ax-replay-build

python3 "$K/scripts/download_archives.py" \
  --out "$RUN_ROOT/archives" --cases pytest-20261026
mkdir -p "$RUN_ROOT/cases" "$RUN_ROOT/workspaces"
test ! -e "$RUN_ROOT/cases/pytest"
tar --no-same-owner --same-permissions \
  -xzf "$RUN_ROOT/archives/pytest.tar.gz" -C "$RUN_ROOT/cases"

python3 "$K/scripts/run_case.py" \
  --case "$RUN_ROOT/cases/pytest" \
  --workspace-parent "$RUN_ROOT/workspaces" \
  --out "$RUN_ROOT/results/pytest-r1" \
  --binary /tmp/ax-replay-build/release/full-fuse-replay \
  --run-id pytest-r1 --worker-id local
```

下载器先检查大小和SHA256。`run_case.py`依次准备初始副本、运行Rust回放、核验最终树；任一步失败即停止，成功时`case-result.json`的`valid`为`true`。

这一步在本地普通目录检查回放。测BrewFS或JuiceFS时，把`--workspace-parent`改为对应挂载内的目录，输入和结果目录放在被测文件系统之外。回放不调用模型；新采集才需要OpenCode和模型配置。

## 正式测试测什么

| 问题 | 测试内容 | 主要指标 |
|---|---|---|
| 能管理多少工作区 | 固定资源下创建、保存和重新打开 | 成功数、创建/重开延迟、元数据内存、空间 |
| 共享和独立修改的代价 | 分叉、版本保存、共享V1/V2资料 | 保存/发布时间、上传字节、请求数、正确性 |
| 多容器运行时表现如何 | 同一批trace的并发与扩展测试 | 批次时间、吞吐、各题耗时、CPU、内存、存储请求 |
| 部分迁移影响多大 | 选中工作区交接，其余持续读写 | 暂停时间、搬运字节、状态正确性、邻居延迟 |

客户端按4、16、64、128个容器扩展，每容器并发4个工作区。保存状态总量另测1千、1万、10万个，按预先规定的时间和资源阈值停止。BrewFS消融必须使用真实实现开关，记录源码和配置。

## 当前状态与结果范围

- **已有：**7份负载及采集/验收记录，Rust回放器，下载校验工具，单任务执行、资源采集、监控配置和结果传输脚本。
- **仍待接入：**完整多容器协调器、统一发令屏障，以及容量、分叉/共享、迁移等机制测试执行器。`campaign.py`生成任务计划，不会启动集群；当前计划仍固定使用旧4份trace，加入新3份需重新冻结任务表。
- **结果：**[完整任务采集与回放样例](results/opencode-full-fuse-pytest-10356-20261023/README.md)、[容器工具验证](scenarios/k8s-containers/validation/README.md)。正式BrewFS/JuiceFS多容器性能对比尚未在本仓库完成。

记录的是挂载点收到的高层FUSE回调，覆盖仓库及HOME/XDG状态；内核缓存命中、网络和镜像运行库不在此范围。回放按已记录顺序执行，耗时包含解析和校验。重复读取比例不是缓存命中率，文件回放成功也不等于原Agent进程恢复成功。

## 其他资料

- [Windows到WSL的OpenCode恢复验证](validation/opencode-resume-20261023/README.md)
- [较早的整体实验计划](execution-plan.zh.md) · [固定输入定义](workloads.json) · [阿里云Terraform](infra/alicloud/README.md)
- [独立早期v1回放工具](https://github.com/Ivanbeethoven/fuse-workload-replay)：与本仓库完整FUSE格式不同。

历史方案中的其他对照系统、16题计划和云主机部署保留供参考；当前多容器测试范围、输入清单及状态以上述入口为准。
