# C1–C4 实验总览

四组分别回答一个实验问题，每组内部比较 baseline。当前准备已有 SSH 环境的脚本，容器/VM/物理机数量由交付清单决定。C1 最近确认的并发上限为8 Agent；其余规模是待调度的设计范围。

| 组 | 实验与 baseline | 条件 | 主要指标 |
|---|---|---|---|
| C1 | 共享后端 trace：Overlay/native lower vs JuiceFS，均 Redis；flat-v1/Overlay 内部对照独立 | 并发1/4/8；三份扩展负载、四份覆盖负载、固定256任务队列 | 任务/s、完成时间、慢化、内容/errno、客户端及服务资源 |
| C2 | 大规模只读 ML 小文件：Pack vs Native flat-v1＋TiKV | 8M文件×1/8/16/32/64环境，另有1M×64；stat/固定读集、冷/热 | 文件/s、访问延迟分布、RPC/GET、缓存、空间、构建/导入成本 |
| C3 | RustFS 无 hint vs B=64KiB/256KiB/1MiB/4MiB | 全局HTTP在途1/16/64；128GiB对象池；全读/4KiB Range、冷/热 | 请求/s、延迟、实际B/q、分层读字节、空间和设备负载 |
| C4 | F：BrewFS fork / JuiceFS clone / 全量cp；M：两文件系统各自stay/move | F到64副本；M到64活跃Agent，迁移既定子集 | 派生完成/首用、恢复首个正确工具、move−stay、邻居影响、隔离与空间 |

C1 流程试跑4个cohort；正式主线84＋内部36。C2设计144、C3设计180、C4设计F108＋M84。正式条件做三个完整配对区组，顺序随机，位置和缓存条件配对。每次交接明确要执行的条件子集。

## 配置

- C1/C4 使用 Redis；C2 Native 使用三节点PD/TiKV；C3直接测对象服务。
- Pack用于C2的只读小文件；动态布局用于C3。C1的BrewFS采用upload_before_commit，JuiceFS客户端writeback关闭。
- 上传策略消融另立study；native-v2异步能力通过资格后再调度。固定总量0–15秒LLM等待默认关闭，按真实工具/轮次边界单独配对。
- 同一批冻结源码、产物、输入、服务预算与缓存口径。baseline顺序复用服务池；并行比较需要独立等配置池。

## 环境与操作

[SSH-RUN](runbook/SSH-RUN.zh.md) 是统一运行入口。清单描述SSH alias、容器/VM身份、Agent槽位、共享宿主、服务和观测入口。不同容器可共用地址和端口映射；宿主关系用于容量核算和结果解释。

C1 每Agent＋FUSE＋回放器合计2 CPU/8 GiB。后端、磁盘和协调资源按实际输入峰值和观测开销确定；旧5台168核方案已暂停采购。实际机器数不由并发数直接推算。

| 要查什么 | 文档 |
|---|---|
| C1设置、方法、指标 | [C1](C1.zh.md) |
| C2–C4完整条件 | [SCALE](SCALE.zh.md) |
| Agent分工与交接 | [CLUSTERS](runbook/CLUSTERS.zh.md) |
| 输入与摘要 | [DATA](runbook/DATA.zh.md) |
| 原生配置和字段 | [ADAPTERS](runbook/ADAPTERS.zh.md) |
| 指标与采集验收 | [OBSERVABILITY](runbook/OBSERVABILITY.zh.md) |
| 环境交付 | [DEPLOYMENT](runbook/DEPLOYMENT.zh.md) |

完整回放、内容资格、观测覆盖、排空/远端读回和结果SHA共同构成性能验收。原始结果包含全部成功、失败、慢值和缺测。
