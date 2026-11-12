# A 池停用：归属核验与冷备份

2026-11-12 12:08:49 UTC，阿里云只读查询确认三台 A 池节点均为 `Stopped / StopCharging`。原创建回执的 `preexisting=false`、实例 ID、请求号、文件 SHA 和 [创建后冻结的完整盘集合](../release-owned-sets-02.json) 均对得上。本次 [归属回执](ownership.json) 保存了逐实例的查询请求号和九块盘的当前属性；没有执行删除，也没有确认备份已完成。

| 角色 | 可释放的实例（完成备份后） | 系统盘（40 GiB） | 随实例释放的数据盘 |
| --- | --- | --- | --- |
| Client | `i-bp1809mw7rlujtj9f705` | `d-bp1809mw7rlujtjc8eph` | `d-bp1809mw7rlujtjc8epi`（100 GiB） |
| Metadata | `i-bp1gy3cfuj9jik0y4oc3` | `d-bp1gy3cfuj9jik0upt3j` | `d-bp1gy3cfuj9jik0upt3k`（60 GiB） |
| Objects | `i-bp1h9kaoya0predubdkn` | `d-bp1h9kaoya0predx6kvc` | `d-bp1h9kaoya0predx6kvd`、`d-bp1h9kaoya0predx6kve`、`d-bp1h9kaoya0predx6kvf`、`d-bp1h9kaoya0predx6kvg`（各 60 GiB） |

九块盘合计 520 GiB，均按量付费、附着原实例且 `DeleteWithInstance=true`。释放前再次逐 ID 查询；如实例、状态、盘集合或随实例释放设置有变化，停止处理该节点。

跳板机 `i-bp1cp8ccqor34u2bo5os`、其原系统盘 `d-bp1cp8ccqor34u2bn4ib` 和本任务建立的控制盘 `d-bp166nue4ac1q7e5oar2` 全部保留。控制盘保存 `state.sqlite` 和已归档证据。EIP、VPC、交换机、安全组、密钥对、OSS 与任何未明确证明由本任务创建的资源都不在清单内。旧 B 池、C1 临时盘与 On1 临时盘不在本次操作范围。

先持 A 池物理锁，再只启动备份所需主机。不要调用会恢复业务挂载或启动 RustFS / Redis 的 `a-pool-up.py`。冷清点必须在目标主机的私有挂载命名空间运行；脚本核对实例 ID、系统盘及其实际根分区、所有原数据盘序列号，并拒绝已挂载的数据盘及仍在使用的实验服务。历史硬件回执提供盘序列号和原挂载点，没有文件系统 UUID；首次冷扫记录 UUID，后续打包必须与该次清点完全一致。

```bash
sudo unshare --mount --propagation private -- python3 /path/preserve-a-backend.py \
  scan client --common /path/preserve-b-backend.py --tag ax-a-cold-01
```

同样清点 `metadata`、`objects`，取回每台 `/var/tmp/ax-a-retire-ax-a-cold-01-<role>/inventory.json`。A 脚本复用 B 脚本的只读挂载、归档和成员校验；从 Git 对象 `00779c70ec0d0a0a4fd1f392cc688cdd18dc6136:experiments/unified-suite/pilot-aliyun/scripts/preserve-b-backend.py` 取出依赖，SHA256 必须为 `038b673b396109f6d72fb09721c2d2d81b793900f9887fb844b609943a70d8c8`。清点只记录文件名、大小和身份；未知子树以完整根路径列出，不能默认忽略。

删除前保全以下原件。先以冷清点确认实际存在与大小；表中有些条目已有摘要或校验回执，不能据此断言完整原件有异地副本。

| 位置 | 优先保全和核对的原件 |
| --- | --- |
| Client 数据盘 `/srv/ax-client` | `o/`、`q/`、`preparation/`、`qualification/` 下的试跑/失败、observer 样本、完整日志和冷验记录；重点核对 C2 `c2-current-a-20261112t093043z`（发布超时）、`c2-pubdiag-20261112t115429z`（保留诊断输出）以及 C4 A→B 的源端 export 和退出记录。C2 trace 原始输入可在 OSS SHA 校验后判定可重建；本机派生输入、回放输出和 producer 日志不能随输入一起默认排除。 |
| Objects 四块数据盘 `/srv/ax-objects-0..3` | 观测样本、`rustfs-logs/`、RustFS 版本/部署/测试回执，以及对象原件。逐盘核对 `rustfs-data/` 内实际桶与保全路线；至少核对 C2 超时留桶 `axc2-e5dd9e90a54244ae` 和最后诊断候选 `axc2diag-6c488e741a194c6c869e9add1a1a7877` 是否存在。后者在 intent 里只是候选名；以最终回执和现场为准。无法证明可重建的已写入或部分写入对象，应保留四盘对应原件。 |
| Metadata 数据盘 `/srv/ax-metadata` | `o/`、`q/`、`preparation/`、Redis 配置和实验日志；`redis/` 内的 AOF、清单和 RDB 是实验状态，逐项确认完整私有副本或可重建证据再排除。不要启动 Redis 读取。 |
| 三台原系统盘 | `/srv/ax-receipts`、`/srv/ax-logs`、实验部署目录（如存在的 `/root/ax-pool-a-bootstrap-01`）、实际 Redis 配置和 unit；补取实验相关服务日志。系统盘只查相关目录和配置，不复制无关系统文件。 |

对每个未知根给出精确 `--include <path>`，或经独立核验后用 `--exclude <path>=<外部副本或重建回执SHA256>`。用新标签重扫并复核；用相同分类、相同标签和 `--scan-sha256` 执行 `pack`。脚本拒绝未知路径、清单漂移和超出指定体积的打包。原包可能含内部配置与会话内容，只放私有存储。独立副本运行 `verify` 后，比对源包 SHA、成员数和接收位置，再决定释放；公开仓库只留可公开摘要和校验值。
