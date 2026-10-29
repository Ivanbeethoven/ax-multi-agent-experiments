# 扩展实验：逐步准备与监督

先读 [64/128 Agent 方案](../SCALE.zh.md) 和 [硬件预算与验收](../HARDWARE.zh.md)。当前只准备，不分配资源或启动 cohort；收到具体试跑调度后按其范围执行，[首次 C1 prompt](agents/C1-pilot.prompt.md) 限定 5 节点、1→8→16。一个负责 Agent 监督本组全体工作 Agent；不能给每个工作 Agent 独立修改 baseline 的权限。

| 步骤 | 入口 / 产物 | 监督检查 |
|---|---|---|
| 1 计划 | scale_plan.py 输出 scale-plan.json | C/基准/规模/隔离/provider/种子、预算、有效条件，所有 execution=false |
| 2 角色和预算 | 填实际 inventory、VM/容器/物理宿主映射、服务端角色 | 不使用旧 9 台模板冒充大规模清单；核峰值、备用节点、网络、费用/所有权；完整保存精确 ID |
| 3 版本和容量 | 复用 RUN 的固定版本、构建分发、只读主机检查；hardware-readiness.json | 逐角色核 CPU/内存/盘/网络；每卷峰值后 25% 空闲与 inode；C2 两臂真实占用、C4 cp 容量、输入/解析/观测余量；基础主机检查通过不代替本页验收 |
| 4 输入与环境 | 完整 trace/语料/任务映射、独立 workspace/HOME/XDG、cgroup | 同一服务池，写者/缓存/会话不串；下载/全量核验在计时外 |
| 5 可选等待 | trace_think_plan.py，一 Agent/任务一计划 | 默认关；只有多 trace 回放可开；明确 T、可信边界、每段≤15 s、总和=T、两臂共用 SHA |
| 6 单 Agent 资格 | 原 Rust 回放、fs_step/session_check 等单步入口 | 完整性、语义、资源与数据路径；不以吞吐排名作为准入 |
| 7 cohort 就绪 | 分布式准备收据/屏障/活跃度监测适配（待实现） | N 个环境同时就绪，核实际并发曲线；协调器只在全部通过后一次放行 |
| 8 一个 cohort | N 个独立进程/容器或 VM 一次执行 | 不在计时路径插入监督 AI 思考；记录每 Agent 生命周期，失败不自动重投 |
| 9 收集与下个条件 | 每 Agent 输出＋cohort 汇总＋SHA/失败表 | 全体终态、原值和保全；再重建初态换臂/升档，禁止按有利结果选子集 |

计划命令每次只写一份文件：

```bash
python3 bench/scale_plan.py --profile main --cluster C1 --isolation docker \
  --provider provided --output "$RUN_DIR/C1-scale.json"
```

128 Agent / 32M 文件等扩展用 --profile expanded；独立 VM 用 --isolation vm；阿里云用 --provider aliyun。其他组替换 C 参数。可加 --include-llm-simulation **额外规划**多 Agent trace 的有等待配对条件；T 仍为空，须在调度单明确后逐任务生成 think plan。该参数不会启动任何回放。

旧 final_plan.py / experiment_inventory.py 对应基础资格组；规模矩阵使用 scale_plan.py，输出中的 placement_templates 只是逻辑位置，actual_host_id 尚空，不是可部署清单。主会话必须另行批准实际资源映射。

现有 experiment_step.py 一次只执行一个已审阅命令；不能用它把 N 个实例顺序启动就算同时并发。尚缺的 cohort 屏障/观测、并发 S3、burst fork、批量迁移适配必须先补齐并小规模验证。不会用 sleep 伪造就绪或结果。工具入口和未完成项按 [RUN](RUN.zh.md) 保留。
