# 下一批小规模实验的服务器配置

本配置用于新建单节点 RustFS pilot；不修改已启动批次的 inventory、报价、二进制或数据盘。正式四节点 RustFS 仍按 [current/CONFIGURATION.zh.md](CONFIGURATION.zh.md) 部署，不能将两种拓扑混为一组结果。

| 角色 | 每台规格 | 数据盘 | 启用时间 |
|---|---|---|---|
| 协调者／跳板 | 复用 2 vCPU / 8 GiB | 100 GiB，独立于系统盘 | 全批；唯一公网入口 |
| 执行客户端 | 4 vCPU / 16 GiB | 100 GiB PL1 | 当前需要的负载槽 |
| 迁移目标 | 与源客户端相同 | 100 GiB PL1 | 仅跨机测试阶段 |
| Redis | 4 vCPU / 16 GiB | 60 GiB PL1 | 本池运行期间 |
| RustFS | 8 vCPU / 32 GiB | 4×100 GiB PL1 独立盘 | 本池运行期间 |

新业务节点系统盘保持 40 GiB PL0。客户端每个 Agent 固定 2 vCPU / 8 GiB / swap=0，单机 pilot 只放一个 Agent；FUSE 和采集开销分别记录。增加并发先增加客户端，不能悄悄增加每个 Agent 的资源。

小规模 Redis 的 maxmemory 为 10 GiB（10737418240），保留 appendonly=yes、appendfsync=always、save=""、noeviction、自动 AOF 重写。正式 64 GiB Redis 节点仍使用 48 GiB maxmemory，两个档位不可混用。

## 为什么这样改

- RustFS 最满盘的七副本容量预测达到 78.6%，离 80% 上限只剩约 0.84 GB。因此单节点 pilot 新盘改为 100 GiB；不据此承诺吞吐提升。
- Redis 盘实际约占 1.14 GB、内存约 805 MB。AOF 重写导致前后占用差失效，应修复容量预测，而非扩容后忽略失败。
- BrewFS 随机写约 108 ms 的同步延迟有软件等待线索。CPU、内存扩容不能替代验证。
- 10 万文件串行冷验约 68 分钟。先拆分元数据、读取和校验时间；核验并行度的改变必须另记协议，并对所有对照一致应用。

依据：[容量原档](../pilot-aliyun/evidence/preparation/c4-flat-aof-capacity-stop-01/README.zh.md)、[随机写诊断](../pilot-aliyun/evidence/preparation/c1-sync-delay-audit-01/README.zh.md)。

## 开机和测量前

1. 单节点 pilot 的 cloud.py 新建和询价默认已改为四块 100 GiB PL1；重新 quote 后再生成本批配置、inventory 和预算，不复用旧 60 GiB 报价。它仍是历史协议部署工具，不是新 current suite 的适配器。
2. 准备二进制和输入后再开节点。完整配对放在同一池，所有臂采用相同硬件；改规格后重新校准，旧批次单独保留。
3. 按最满单盘检查字节和 inode，保留至少 20% 空间。包括完整副本、临时对象、失败现场和归档副本。
4. Redis 从导入前就记录 AOF 当前大小、重写次数、状态、文件代际和分配量，以及磁盘／内存峰值。AOF 缩小时保留原始观测，不能将负差截成零后放行；原容量失败仍须解决。
5. 归档压缩和大批上传安排在共享资源空闲时。先核对首盘多出的文件归属；只清理已归档并核验的本批临时文件，不删除对象数据目录。
6. 采集逐核 CPU、cgroup throttling、RSS/内存峰值、磁盘 IOPS/吞吐/等待、网络吞吐及服务延迟。只有实测持续触及对应上限，才升级该节点。网络和磁盘性能同时核对实例上限；不把 vCPU 数增加等同于单核速度增加。

每批结束核验归档后释放新增实例与随实例盘，保留原跳板和 OSS。
