# 阿里云资源准备

规模更新：主实验按 [64/128 Agent 方案](../../experiments/retest-2026-10-29/SCALE.zh.md) 区分 Docker 节点与一 Agent 一 ECS；下文 experiment_inventory.py 的 9/3 台模板仅用于基础资格。规模资源仍须明确实际清单/费用范围，当前不开跑。

测量方法统一见 [RUN](../experiment/RUN.zh.md)，分工与 9 台/3 台拓扑见 [CLUSTERS](../experiment/CLUSTERS.zh.md)。本文件只规定云资源差异，不再维护第二份测量流程。

1. `python3 harness/experiment_inventory.py template --provider aliyun --mode parallel --output "$RUN_DIR/inventory.json"` 生成待填清单；此操作不调用云 API。
2. 在调度单明确数量、实例规格、地域/可用区、费用上限与执行范围后才分配资源。使用当前价格/库存；旧实验实例 ID、盘、SG 和桶不作为新批次资源。C1 的配对机器使用相同型号与磁盘配置。
3. 原则上复用一个批准的管理入口，worker 走私网/ProxyJump；SSH 来源精确到批准地址。输入/产物优先同地域 OSS internal 或私网分发。严格核 host-key。
4. 保存实际 ECS/盘/SG/来源规则/桶/vSwitch/keypair 的精确 ID、归属与预先存在标记；将已部署资源映射到统一 inventory。云清单与实验分组共同确定操作范围，不能仅按名称批量处理。
5. 在每台执行统一 `host_preflight.py`。四块云盘应呈现不同块设备；这只证明独立云盘，不证明物理主机或物理介质故障域独立。M 若要报告物理跨机迁移，另留置放/宿主证据，否则称“跨实例”。
6. 后端沿用 [生成器](deploy/generate_rustfs.py) 和 [节点检查](deploy/rustfs_node.py)，传入统一手册的 `experiment` 字段；先生成、检查，之后单独启动。镜像和客户端实际摘要、四开关、EC 与 cgroup 留运行证据。

测量不自动重试抢占中断，也不自动补购节点。结果保全并确认进程/FUSE/服务结束后，再按精确清单独立释放本批资源；预先存在或共用资源由主会话处理。查询确认释放后才标记 cleaned。

旧 [parallel 控制器](parallel) 和 `dispatch.py` 对应历史整批运行，保留用于复现，不是当前 AI 分步监督的启动入口。
