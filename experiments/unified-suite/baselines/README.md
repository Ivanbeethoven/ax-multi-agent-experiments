# 按 baseline / BrewFS 路径接入

协调者先读[统一入口](../README.md)，每个集群执行者再选择下表对应文档。每份入口定义本路径的arm身份、工作负载范围、导入/挂载差异和证据要求；共同执行步骤在本页维护。

| 路径 | 独立入口 | systems计划中的arm | 默认pool |
|---|---|---|---|
| JuiceFS | [JuiceFS](juicefs.zh.md) | `juicefs`、`juicefs-stay`、`juicefs-move` | pool-b |
| BrewFS native flat | [native](brewfs-native.zh.md) | `native` | pool-a |
| BrewFS native workspace | [native-workspace](brewfs-native-workspace.zh.md) | `native-workspace`、`native-workspace-stay`、`native-workspace-move` | pool-a |
| BrewFS entity native lower | [entity-native](brewfs-entity-native.zh.md) | `entity-native` | pool-a |
| BrewFS entity packed lower | [packed](brewfs-packed.zh.md) | `packed` | pool-a |
| BrewFS技术点与缓存消融 | [消融入口](brewfs-ablations.zh.md) | 独立ablations计划中的精确arm名 | 按arm映射 |

外部baseline只有JuiceFS；其余为候选路径、内部对照或消融。入口文档不创建独立实验条件，不改变[方法矩阵](../02-ablations.zh.md)。

## 接收本池任务

协调者交付实际`RUN`、`POOL`、`OWNER`、plan及摘要、`drafts/queues.json`、inventory、资源账本、原始stage/进程回执和本次新lease输出路径`LEASE`。执行者先读[环境](../03-environment.zh.md)、[输入](../04-inputs.zh.md)、[单步执行](../05-execution.zh.md)及[证据收尾](../10-evidence.zh.md)，然后核本入口的路径差异。

**同一个pool只有一个执行owner。** 默认四条BrewFS路径共用pool-a的一条队列，由该池执行者根据领取到的arm切换入口文档。要让native、workspace、entity-native、packed各自并行，协调者必须在冻结前将它们映射到不同实际集群，填写独立inventory/resource IDs；[集群配置](../12-parallel-baselines.zh.md)说明product/arm覆盖关系。不能仅分配四份文档就在同一pool启动四个执行者。

先核本池实际资产和资格。导入/完整base校验按实际pool/dataset/representation/layout一次记账；不同池的相同路径需要各自的真实asset回执。共用硬件/盘/服务的池不得同时准备或测量。跨池配对仍需`cross-pool-hardware-equivalence`，固定集群分配的限制保留。

## 按本池cell执行

以下命令在同一个协调者主机执行，变量来自实际交接单。各集群通过既有runtime远程运行stage，不复制控制端DB。

```bash
# LEASE必须是本次新的输出文件；已有lease先读原记录，不覆盖。
python3 -m bench.suite status --db "$RUN/state.sqlite"
python3 -m bench.suite claim --db "$RUN/state.sqlite" --pool "$POOL" --owner "$OWNER" --output "$LEASE"
```

claim按pool领取，**没有按baseline过滤的claim参数**。核lease.cell的`arm_id`、`arm`、`pool`、`asset_id`、params和required_gates；混合队列若领到另一条路径，先打开对应入口，不能改cell或另开状态库跳过它。`cell:null`时诊断资格/资产/共享资源；`already_claimed:true`先核原stage，不重复启动。

领取后按cell.stages及[单步命令](../05-execution.zh.md)执行，C3每个workspace有独立生命周期。创建/挂载/ready、预热、读写、COW/fsync、强排空、普通卸载、真实退出及验证分别留证。预检/资格时核本批二进制原生帮助和实际能力，不把规划字段当成已经实现的命令。

本cell原始结果下载、归档逐成员核验和进程/挂载/债务关闭后，使用原lease执行finish，释放本cell调度占用。另一baseline的锁和结果不受影响；VM/盘等资源按[已有授权与账本](../10-evidence.zh.md)另行释放。故障、恢复、慢阶段和等待成本保留；结果由协调者按同条件/轮次汇总。

## 交接给下一位执行者

交接[CURRENT.json](../11-ai-handoff.zh.md)中的本池owner、原lease、当前stage、原PID/boot/start tick、输出路径及下一单步动作，并标出使用的这份入口。未知或started不回pending；不重新导入、clone或重放已开始的测量。共同阶段的后续变更写回系列文档，本入口只维护路径差异。
