# BrewFS native workspace 执行入口

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

负责`workspace-native-v2`内部对照及原生流转。先读[共同执行步骤](README.md)，默认pool-a；独立集群执行时按下面全部arm名分别配置arm_pools。

## 身份与工作负载

| arm | 范围 |
|---|---|
| `native-workspace` | C2完整trace、C4 fork |
| `native-workspace-stay` | C4不迁移对照 |
| `native-workspace-move` | C4跨机原生会话流转 |

product为`brewfs`，engine/volume_format为`workspace-native-v2`，使用原生lower和workspace。`arm_pools["native-workspace"]`只覆盖精确同名arm；若三个arm都放同一独立池，须同时映射stay/move，或者保持product回退映射覆盖实际池。

## 本路径准备

1. 使用本批实际支持的原生base publisher/导入，记录不可变revision、native base对象前缀及writer释放，完整base验证一次。参见[输入](../04-inputs.zh.md)，不猜packed/native manifest来代替revision。
2. 原生JSON中固定`volume_format=workspace-native-v2`、`native_base`、真实`workspace`及`workspace_namespace`；新child使用新workspace身份。同步上传、缓存/FUSE与metadata字段按[配置约定](../examples/README.md)和plan核对。
3. 挂载使用BrewFS prepare适配器和原生config，不加experimental entity参数。本批binary需实际支持原生workspace创建和重开；原help、创建、ready、writer lease及退出回执进入资格。

默认共享lower派生；`delta-eager`为[独立消融](brewfs-ablations.zh.md)，需要同引擎eager物化适配器和自己的资产/回执，不在本arm里临时切换。

## 测量与交付

[C2](../07-trace.zh.md)每任务从同一冻结initial tree派生私有upper；[C4 fork](../09-fork-handoff.zh.md)保留派生到首次可用、首次COW、父兄弟隔离及物理增长。

C4 stay/move核实际不同执行宿主、相同checkpoint、单writer fencing、源端排空/释放、目标端原生重开和首个新工具完成；保留全部暂停和恢复开销。不能将fresh namespace或重放完整任务当作原生handoff。系统目录未给本路径安排C1/C3，不自行扩展cell。

逐cell按[共同步骤](README.md)封存和关闭；性能只与相同条件/轮次结果配对，恢复或gap保留失败标记。
