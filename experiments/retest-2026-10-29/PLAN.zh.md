# C1–C4 实验总览

**已统一准备阿里云 ECS 与自备 Linux 服务器两版；现在不开跑。** C1–C4 是四个实验设置与负责组，每组内部比较多个baseline。无需Kubernetes，默认一Agent一Docker，独立VM单独成批。

| 组 | 测什么 / baseline | 主规模 | main cohort |
|---|---|---|---:|
| C1 | 共享后端Agent trace：BrewFS Overlay/native lower vs JuiceFS，均Redis；flat/Overlay内部对照单列 | 1/8/16/32/64 Agent；固定256任务队列 | 主线132＋内部54；pilot另6 |
| C2 | 海量只读ML小文件：Pack vs Native flat-v1 + TiKV | 8M×1–64；1M×64；冷/热、stat/固定读集 | 144 |
| C3 | RustFS默认无hint vs B=64KiB/256KiB/1MiB/4MiB | 1/16/64全局HTTP在途；128GiB对象池；全读/4KiB Range、冷/热 | 180 |
| C4 | F：fork/clone/全量cp；M：BrewFS/JuiceFS各自stay/move | F到64副本；M到64活跃Agent，迁移既定子集 | F108＋M84 |

三个完整配对区组，主矩阵合计**702**；expanded为**1086**，另含128、C2 32M、C3大对象/1TiB池。计划数不等于已就绪数。Pack只在C2，C3的HTTP并发不是Agent数量，C4保留全量cp对照，不新增共享目录实验。

**上传策略独立 study=writeback：C1 186、C4 90。** 它与C1同步baseline的132+54=186无关，不用旧成绩抵扣。默认BrewFS upload_before_commit / JuiceFS客户端writeback关闭；native-v2异步能力仍须完成，不通过则保留阻塞。LLM固定总量0–15s间隔默认关闭，后续按工具/轮次边界独立配对。

## 资源与两版入口

| 集群 | 64 Docker | 128 Docker | 64独立VM | 128独立VM |
|---|---:|---:|---:|---:|
| C1 | 11 | 19 | 67 | 131 |
| C2，含3个PD/TiKV节点 | 13 | 21 | 69 | 133 |
| C3 | 4 | 4 | 4 | 4 |
| C4，含另一节点上的目标池 | 19 | 35 | 131 | 259 |
| **并行总数** | **47** | **79** | **271** | **527** |

C1试跑5台、168 vCPU/672GiB。完整64 Docker四组合计1536vCPU/6144GiB。C4的400GiB全量复制需要80/160TiB原始容量目标；C2容量须从真实语料核算，节点数不代表存储足够。具体SKU、盘与进程配额见[部署手册](runbook/DEPLOYMENT.zh.md)。

| 项目 | 阿里云 ECS | 自备服务器 |
|---|---|---|
| 计算 | --provider aliyun；按角色生成ECS候选与待填资源清单 | --provider provided；同一逻辑实验，映射已交付物理机/VM，无云API |
| 网络 | 私网测量，尽量一个公网管理入口；精确来源与host-key | 已批准内网与可信SSH；核真实宿主/共机竞争 |
| 存储 | 独立云盘，核实例总IO限额；不是四目录 | 已批准独立设备/目录，不默认允许格式化 |
| 统计 | 独立campaign与配对结果 | 不与不同云硬件混成同一重复 |
| 收尾 | 保全后按本轮精确ID释放并查询确认 | 保留主机、原数据和原有服务，只处理本轮所属环境 |

## 阅读顺序

1. [ECS/自备服务器部署与脚本规划](runbook/DEPLOYMENT.zh.md)：四组硬件、完整步骤、已实现与待补适配。
2. [C1具体方法](C1-ALIYUN.zh.md)；[C2–C4条件与指标](SCALE.zh.md)。
3. [27份trace数据交接](runbook/DATA.zh.md)：Drive/OSS、可信摘要、约408MB pilot与9分卷恢复。
4. [负责人、调度单与四份prompt](runbook/CLUSTERS.zh.md)。
5. [Manager能力与安全边界](runbook/MANAGER.zh.md)、[跨节点流转](runbook/MIGRATION.zh.md)。

计划入口为[scale_plan.py](../../bench/scale_plan.py)的 **--campaign ecs-20261101**，沿用原生成器而非新增harness。旧legacy-20261029保留历史复现，旧618/990、9/3节点和3CPU后端预算不用于本轮。原始证据、失败记录和本地数据不删。

实际非空lower/mount、三节点TiKV、分布式屏障与聚合、并发S3、批量fork/会话交接仍须按步骤补齐并资格验证；文档、生成器和PR完成不等于实验ready。
