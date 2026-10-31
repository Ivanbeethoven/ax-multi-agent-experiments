# M：Agent 跨节点流转

本页描述两节点恢复资格。64/128 Agent 的迁移子集、备用池和邻居干扰指标见 [扩展方案](../SCALE.zh.md)，另用 scale_plan.py；保持同 checkpoint 的 stay/move 对照。

负责人 C4。比较 **BrewFS Overlay 与 JuiceFS，每种各有 stay / move**，不是只测 BrewFS。两道已有来源任务 Pytest-10356、SymPy-13878 × 2 filesystem × 2 去向 × 3 区组＝24 个单元。新 benchmark 需另冻结任务和评分器，不临时替换。

## 设置与边界

固定两个执行节点 E1/E2、一个持久服务节点 S。S 托管本轮隔离的 RustFS 和元数据，始终保持原位置；同一时刻只测一个 filesystem。BrewFS 使用 native lower/upper，不带 Pack/dynamic hint；JuiceFS 使用原目录，目标重新挂载同一 namespace，不要求 clone 或全树拷贝。

M 中客户端预算 4 CPU/8 GiB 在当前执行节点；元数据仍计入这 8 GiB 总额，须预先拆分并固定（例如客户端 3 CPU/7 GiB＋服务节点元数据 1 CPU/1 GiB），不可双方各拿 8 GiB。RustFS 在 S 为 3 CPU/16 GiB。两臂相同网络、持久性、cache 总额，记录实际跨机 RTT 与资源限额。

每个 filesystem 的 stay/move 从**同一个已完成工具调用的 checkpoint** 分叉。stay 也要停客户端、flush、释放写者、清空本轮客户端缓存并恢复会话，只在 E1 继续；move 在 E2 继续。复原 checkpoint 的准备成本单列。两者均使用同一恢复提示、剩余模型预算与评测期限，禁止从初始提示重跑冒充续跑。

只测干净工具边界的应用状态交接，不迁移进程内存、打开的 FD 或执行中的工具；不包含断网抢占/崩溃接管。不同 VM 不自动等于不同物理宿主。多级共享目录不在本轮。

## 单步执行

Manager #390 的 [实际能力与接入步骤](MANAGER.zh.md) 仅覆盖本地 SQLite catalog 的创建/fork/逻辑删除，未提供 checkpoint、drain、attachment 或 handoff。本页步骤 02–07 仍由原生适配器完成；不调用草案 API，不用 DELETE 代替释放写者，也不因 Manager 已合并而把两节点恢复标为通过。

| 步骤 | 操作 | 必须检查 |
|---|---|---|
| 01 资格 | 本机最小会话，再跨 E1/E2 恢复；分别对两个 FS 做 | 保留 session、历史、工具返回、未提交代码、HOME/XDG；空状态负对照不能凭初始提示“恢复” |
| 02 checkpoint | 完成当前工具、关闭 Agent/子进程，原生 `opencode export SESSION`；[session_check checkpoint](../../../bench/session_check.py) | 无进行中工具；export 只证明历史，另查实际 PID、SQLite/WAL、远端写入 drain 和 lease |
| 03 文件状态 | [fs_step snapshot](../../../bench/fs_step.py) 为 repo、所需 HOME/XDG 分别生成清单；[experiment_step seal](../../../bench/experiment_step.py) 保全选定状态 | 输出在被测树外；不公开 auth/模型密钥；源和目标路径布局一致 |
| 04 释放 | 单独停止源 mount，观察写者 lease 释放及无活动客户端 | BrewFS 的 `workspace migrate` 是 schema 操作，不是此实验；禁止用它替代交接 |
| 05 目标准备 | 原生 mount 同一工作区；按当前 OpenCode 版本原生 import/export 或打开已安全转移的 DB | 不复制整个共享数据树；传输的仅必要本地会话状态。原版本 export/import 格式先做资格验证 |
| 06 恢复核对 | `fs_step check`；目标 export 后 `session_check restored` | 文件/目录/内容/权限一致，session ID 和完整历史前缀相同；不是只看命令退出码 |
| 07 续跑 | 原生 `opencode run --session SESSION ...`，使用冻结的统一恢复提示；只执行一个单元 | 第一条真实工具必须续接原任务；记录首次正确读写和剩余预算 |
| 08 验收 | 新 export 后 `session_check resumed`；独立评分器、最终补丁和副作用审计 | 新完成工具、无丢历史/重复外部动作、任务结果；导出检查不替代真实任务正确性 |

原生 CLI 的确切参数以冻结版本 `--help` 和资格结果为准；[旧 resume probe](../../../scripts/opencode_resume_probe.py) 仅是 Windows→WSL 参考，其超时和自动推进不能直接继承。本仓库新脚本负责单步证据与核对，不替 Agent 自动操作所有节点。

## 计时与输出

协调端使用同一个进程、同一 monotonic 时钟，记录请求到达、工具边界到达、drain 完成、写者释放、目标就绪、首条正确工具读写的 ACK。每个 ACK 以协调端收到时间计；各节点本地阶段耗时可另报，不能相减不同机器的 monotonic 时间。

主要报告总暂停时间（含等待工具边界）、各阶段耗时、首工具恢复延迟、恢复后访问性能、传输字节、token/任务预算、独立任务成功率。restore/文件完整性扫描会预热客户端：资格扫描放计时外；正式测量用已资格验证的恢复路径，验收扫描在首次工具计时后执行，冷态定义对 stay/move 一致。

AI 在单次计时前审阅动作与恢复适配器，计时区间不插入人工确认或 LLM 监督等待；单次交接适配器只完成这个 checkpoint 的交接，不推进下一任务/条件或自动清理。若当前只能人工逐步交接，结果标为人工流程演练，不能作为文件系统暂停成绩。协调端事件采集与实际原生会话适配必须在真实服务器资格验证时补齐。

每个区组保存两种 filesystem 的四个原值和对应 checkpoint 标识；分别给 move−stay 差值及文件系统之间差值。任务失败、历史变化、重复副作用、工具未续接均单列，不记作 0 秒。真实两节点恢复资格未通过时只报告 blocked，不生成迁移成绩。
