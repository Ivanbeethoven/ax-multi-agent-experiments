# 自备 Linux 服务器

实验方案、baseline、输入、指标、脚本与阿里云版本相同，使用 [统一分步手册](../experiment/RUN.zh.md)。推荐 9 台并行；最低 3 台顺序复用，分配见 [四组拓扑](../experiment/CLUSTERS.zh.md)。当前不要求提供地址，不连接或修改任何服务器。

## 提供条件

| 项目 | 要求 |
|---|---|
| 计算 | 建议每台至少 8 核 / 32 GiB；同一比较的 CPU 型号、NUMA、系统/kernel、磁盘类型相同；记录 CPU governor、虚拟化与后台负载 |
| 存储 | RustFS 主机 4 个独立、已挂载的 XFS/ext4 数据设备，每盘名义容量至少 250 GiB；不能用同盘四分区或四目录。n07/n08 仅运行客户端，可只提供专用本地工作盘 |
| 工作区 | 每机另有本地输入、缓存、元数据与结果空间，建议至少 100 GiB；不写进 RustFS volume。Agent 解包前按实际清单核可用容量/inode |
| 网络 | 节点私网互通，记录链路速率、RTT；M 的客户端至同一服务节点走相同链路。服务器是否处于不同物理主机另留证 |
| 运行环境 | Python 3.11+、Docker/Compose v2、FUSE3、fio、Git、util-linux；D 另需 boto3。镜像和二进制统一按 SHA/digest 分发，测量期间不安装/构建 |
| 权限 | 提供 SSH 别名、可信 host-key、批准的目录/盘与必要的有限 sudo；cgroup v2 可用。不得默认这些机器可以重装或格式化 |

## 准备顺序

1. 生成未填拓扑：`python3 harness/experiment_inventory.py template --provider provided --mode parallel --output "$RUN_DIR/inventory.json"`。3 台改 `sequential`。此模板不可执行，空字段必须填成实际信息。
2. 填节点 SSH 别名、私网 IP、资产编号、已核 host-key、硬件类别、挂载点、工作区及容量下限。`resource_id` 是提供方资产号；不填 ECS ID，不复用历史机器清单。
3. 用管理端专用 SSH 配置连接；设置 `StrictHostKeyChecking=yes`、`UserKnownHostsFile`、`IdentitiesOnly=yes`、`BatchMode=yes`，有跳板再填 ProxyJump。公钥指纹由可信管理渠道核对，不能把首次扫描自动当可信。
4. `experiment_inventory.py check` 后，在每台运行 `host_preflight.py`。主会话对比实际 CPU/盘/内存/kernel，而非只相信填写的 `hardware_class`。单机检查不证明无人使用，还须查进程、FUSE、容器、端口与已知服务归属。
5. 按 [统一手册](../experiment/RUN.zh.md) 冻结产物，分别准备资源限额、服务、数据和客户端。完成 1,024 文件/小 trace/布局与会话恢复资格验证后，AI 决定当前单元是否就绪。

## 收尾与差异

下载并核验结果后，只清理由该 Agent 创建且列在清单中的挂载、进程、容器、数据库命名空间、桶前缀和工作目录。**不删除服务器、不格式化设备、不清其他用户数据、不关闭提供方已有服务。** 不在共享机执行全局 drop_caches；无法隔离冷态时标记未达到冷态条件。

Aliyun 与 provided 使用独立 campaign，硬件/网络不同的数据分别报告；共用相同逻辑输入与方法，不把两类环境的轮次凑成一个配对样本。提供的机器不足以满足拓扑时保留准备状态，不自行租补机器。
