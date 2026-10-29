# 四集群与 Agent 分工

**主目标已改为 64 Agent / 扩展 128 Agent，每 Agent 独立 VM 或 Docker。** 先按 [扩展方案](../SCALE.zh.md) 和 [SCALE-RUN](SCALE-RUN.zh.md) 填写调度单的 protocol_profile=scale 或 qualification、isolation=docker/vm。下文 9/3 台与 192/48/60/42 计数只属于基础资格池，不可套用到规模测量。四个 owner 管理工作 Agent，不计入被测并发。

参数以 [实验方案](../../../paper-design/FINAL-PLAN.zh.md) 为准，操作按 [分步手册](RUN.zh.md)。C1–C4 是实验设置与资源分组，baseline 在组内比较。每组由一个 Agent 负责；阿里云与自备服务器使用相同合同。当前只有准备代码，未分配或启动新测试服务器。

## 1. 固定分工

| 集群 / 唯一负责人 | 实验 | baseline 与并行安排 | 初始 prompt |
|---|---|---|---|
| C1 / c1-owner | G 产品比较；A Overlay 消融 | G：Overlay/JuiceFS 两机；A：flat/Overlay 另两机。两个池同时测，池内两周期换位 | [C1](agents/C1.prompt.md) |
| C2 / c2-owner | P 海量只读 ML 小文件 | Pack vs Native+TiKV。一个完整区组内在同一 worker 交替测两臂；多个完整区组可独立分机 | [C2](agents/C2.prompt.md) |
| C3 / c3-owner | D RustFS 动态布局 | 无 hint 与四个 B。一个 worker 顺序测完整配对组；不同完整区组可分机 | [C3](agents/C3.prompt.md) |
| C4 / c4-owner | F fork / clone；M Agent 跨节点流转 | F 三臂同机；M 在固定源/目标执行节点与独立服务节点上比较 BrewFS/JuiceFS，各有 stay/move 对照 | [C4](agents/C4.prompt.md) |

基础机制/资格推荐 **9 台 Linux 服务器**；管理由现有工作站承担：

| 节点 | 用途 | 数据盘 |
|---|---|---|
| n01、n02 | C1-G 两机交叉比较 | 每台 4 个独立数据盘 |
| n03、n04 | C1-A 两机交叉消融 | 每台 4 个独立数据盘 |
| n05 | C2-P 完整配对区组 | 4 个独立数据盘 |
| n06 | C3-D 完整配对区组 | 4 个独立数据盘 |
| n07、n08 | C4-M 源/目标执行节点 | 每台至少 1 个专用本地数据盘/文件系统用于输入、缓存和状态 |
| n09 | C4-F 单机；完成 F 后作为 M 持久服务节点 | 4 个独立数据盘 |

最低 **3 台**也可：`mode=sequential`，C1 的 G/A、C2、C3、C4 顺序独占复用；每台准备四个独立数据盘。四个 Agent 可并行准备，计时任务必须由主会话逐组交接。7 台可先跑 G/A/P/D/F，之后将 C1 释放的两台改为 M 执行节点。不能在旧进程/FUSE 未结束或证据未保全时移交。

同一对照硬件、磁盘、网络与系统版本必须相同。自备机实际规格不足时另开规格批次，统一各臂预算，不把不同规格或不同供应商结果混成配对区组。阿里云资源数量/地域/费用范围先写调度单；自备机的机器所有权始终属于提供方。

基础资格池的 C2/C3/C4 扩容只增加执行完整配对区组的 worker；规模组按 SCALE 的共享后端与固定资源预算扩容客户端，不把“方法 X 永远在机器 1、方法 Y 永远在机器 2”当可比结果。机器身份、区组、周期、方法在任务表中明确记录。

## 2. 资源和数据边界

| 对象 | 规则 |
|---|---|
| 云资源 | 每个 Agent 只管理自己清单中的实例、盘、SG/来源规则、临时桶和 keypair；以精确 ID 和归属为准，不按宽泛名称批量删除 |
| 状态 | `runs/<campaign>/<C1..C4>/` 各自保存调度单、配置、事件日志、结果、保全与清理回执；新尝试用新 attempt ID |
| 后端 | 基础资格 G/A/P/D/F 每个 worker 独占 RustFS/元数据/缓存；规模组的所有 Agent 共享本组服务池，按 SCALE 固定预算；C1 的 G/A 不共享可变服务。M 的源/目标访问同一专用持久服务节点，两种 filesystem 顺序使用隔离的 DB/bucket/卷；不把服务随 Agent 一起搬走 |
| 版本与资产 | 主会话先统一冻结；同一构建只做一次，所有 Agent 按 SHA/digest 取用；不能各自更新分支造成版本漂移 |
| OSS 输入 | [7 份 trace 目录](../../../paper-design/TRACE-WORKLOADS.zh.md)只读；新结果不能覆盖输入。公开桶不能直接存私有配置或原始凭据 |
| 网络 | 优先单一管理入口＋私网 worker；严格 host-key 和既定来源；共享跳板由主会话负责，各 Agent 不删除 |

四集群共享的是固定输入与产物，不共享测量中的可变服务。输入分发、哈希扫描、构建、上传安排在计时之外。

## 3. 发给每个 Agent 的调度单

复制对应 prompt 时附一份调度单。至少填写：

```json
{
  "campaign_id": "实际唯一批次 ID",
  "cluster_id": "C1",
  "owner_agent": "c1-owner",
  "phase": "prepare",
  "run_dir": "本集群绝对路径",
  "plan_path": "本集群冻结任务表",
  "plan_sha256": "实际 SHA256",
  "shared_asset_manifest": "已核验产物清单及 SHA",
  "source_pins": "本批 BrewFS / RustFS / JuiceFS 记录",
  "allocation_manifest": "实例/盘/网络/密钥的精确清单与归属",
  "provider": "aliyun 或 provided，与 inventory.json 一致",
  "inventory": "已填实际服务器信息并核验的 inventory.json 及 SHA",
  "resource_budget": "已确定的数量、规格、地域和费用上限",
  "execution_authorization": "人类批准的范围或明确仅准备",
  "result_retention": "本地保全目录及允许的远端输出前缀"
}
```

`phase=prepare` 只核查、补齐单步脚本、生成配置和就绪报告。缺少资源清单时，不自行找历史实例或购机。得到明确执行范围后，Agent 仍先核当前进程和实际资源，不能把旧状态当本轮运行证据。

主会话可分别生成四份计划，以下每次调用只写一份文件：

```bash
python3 bench/final_plan.py --cluster C1 --output "$RUN_DIR/C1-plan.json"
```

C2/C3 替换参数；C4 使用 `--cluster C4 --include-migration`，共 42 个单元（F 18＋M 24）。计划写入负责人、baseline、池、区组和周期；所有 `execution_enabled` 初始为 false。M 先过独立会话恢复资格门，再执行 24 项，不阻断其他组。单次 smoke 不并入正式三区组。

## 4. 每个 Agent 的执行合同

1. 先读 AGENTS、主方案、本文件、自己的 prompt 和分步手册；核调度单与输入/产物摘要，写 `readiness.json`，逐条列出已验证和缺失项。
2. 按“准备一个步骤→执行→读取输出→AI 判定→下一步骤”推进。旧整套运行器只可参考；不得用一个脚本自动跑完所有 baseline、重试并清理。
3. `step-events.jsonl` 记录实际命令（脱敏）、时间、退出码、PID/单元、输出文件与 AI 判定。中断先查真实进程并保全结果；状态不明不得重投。
4. 不为成绩改任务、版本、缓存、重复数或错误规则。失败、未完成、有效慢值都保留；新条件要新批次，不能覆盖原结果。
5. 汇报 `status.json`：`preparing / ready / running / blocked / collected / cleaned`、当前 cell、证据路径、缺失条件。普通无变化无需反复通知。
6. 最后校验原始结果/日志完整性，生成 SHA 清单，下载后再次核 SHA；确认本集群进程、构建、FUSE 和服务结束，再按精确清单清理自身资源。只以实际查询确认的释放结果写 `cleaned`。共享资源留给主会话统一收尾。

主会话负责统一版本、调度单、跨集群冲突和最终报告。Agent 可以修复自己负责的单步入口，但测量中不能静默替换二进制或更改冻结合同。配置缺项先形成可审阅改动和阻断说明，不假装门禁已经通过。
