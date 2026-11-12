# B 池退役：先保全证据，再释放空闲资源

本轮只考虑原 B 池三台。A 池、原跳板和协调盘继续保留。C4 的 A→B 资格必须先退出、取回两端回执并交回同一套资源锁。B client 若保全失败，仍可分别处理通过核验的两个后端。本页为事前清单；实际释放见[退役回执](../b-pool-retirement-01/README.zh.md)。

| 角色 | 实例 ID | 系统盘（40 GiB PL0） | 数据盘（PL1） |
|---|---|---|---|
| objects | `i-bp1bhdngko7nq3c3jrhp` | `d-bp1bhdngko7nq3c2i33z` | `d-bp1bhdngko7nq3c2i340`、`d-bp1bhdngko7nq3c2i341`、`d-bp1bhdngko7nq3c2i342`、`d-bp1bhdngko7nq3c2i343`（各 60 GiB） |
| metadata | `i-bp14nenf7osz9v1snsp2` | `d-bp14nenf7osz9v1rkk5k` | `d-bp14nenf7osz9v1rkk5l`（60 GiB） |
| client | `i-bp1fmmfkcpr8w99j2bsy` | `d-bp1fmmfkcpr8w99pwgc4` | `d-bp1fmmfkcpr8w99pwgc5`（100 GiB） |

[原归属账本](../release-pool-b-freeze-01/result.json)绑定三台按量实例、9 盘、520 GiB，均为 `DeleteWithInstance=true`。11 月 12 日杭州按量报价：40 GiB PL0 ¥0.042/h、60 GiB PL1 ¥0.126/h、100 GiB PL1 ¥0.210/h。后端盘 ¥0.714/h；若三台全释放，磁盘报价共 ¥0.966/h、约 ¥695.52/30 天。以删除后查询和账单为准。询价 API 请求号分别为 `01A1CF38-73DA-54A5-ADF9-3E77F336B779`、`01A1CF38-76B2-579C-853A-0D6B4E750FB4`、`01A1CF3B-8595-53FD-AA12-46BA96E8326B`。

**保全哪些数据。** [旧 C1 `raw_archive_pending`](../b-stopcharging-2118-01/stop/result.json)列出 objects 5 条、metadata 4 条、client 21 条实际路径。另留三机 `/root/ax-pool-b-bootstrap-01/`，数据盘的观测、关闭、失败日志，以及客户端新 C4 目标回执（必须与 C4 独立收尾对上）。Redis 配置只取 AOF、数据库数、目录、内存等重建参数；RustFS 记录镜像与布局开关。原始包可能有内部配置或会话内容，只有私有副本；公开仓库留筛过的摘要和 SHA。

**哪些可以重建。** [C2 正式和校准](../../c2-jfs-01/README.zh.md)有独立 OSS 下载校验，原 `pytest-full-02` 也有[可下载清单](../../../../../../scenarios/k8s-containers/archives.json)和 [67,577 份输入校验](../b-c2-input-copy-01/verify-publish-stdout)。[C3](../../c3-jfs-01/oss/index.json)和 [C4](../../c4-jfs-01/oss/index.json)有独立协调盘/OSS 结果，输入可从 [确定性生成器](../../../scripts/prepare-cloud-inputs.py)按旧参数重建。[B 池部署记录](../pool-b-services-actual-01/README.zh.md)固定服务和工具；Redis `redis/` 只排除已识别的测试持久化段，RustFS `rustfs-data/` 只排除自身内部目录和证据已指明的准确测试桶。清点时出现其他桶、目录、挂载或特殊文件，记录准确路径并暂不释放该角色；不要用桶前缀猜归属。

**保全窗口。** 持旧 B 池物理锁后才启动指定 B 角色，沿用已运行的 jump；不启动 Redis/RustFS 或测量。预计后端普通文件约 20–30 GB（实际以清点为准），压缩包初估 0.3–2 GB。首轮 15 分钟预算含停机，不足即留回执停机。下面命令在各节点运行；`TAG` 为全新小写标签，`WORKER` 为已核 SHA 的脚本。

```bash
# jump 单独保持打开这个 SSH 会话；回显 held=true 后继续。
python3 -B "$LOCK_HELPER" --repo /srv/ax-pilot-control/code-12 --max-seconds 1200
# 从同一会话输入 check，确认锁仍在；全部收尾后输入 close。

# 各 B 节点 root，用已有跳板私网 SSH，单独执行：
unshare -m --propagation private python3 -B "$WORKER" scan "$ROLE" --tag "$TAG"
# 取回 inventory.json，检查每条 unknown 和 original_pending；必要时重新生成新标签。
unshare -m --propagation private python3 -B "$WORKER" pack "$ROLE" --tag "$TAG" --scan-sha256 "$SCAN_SHA"
```

结果在 `/var/tmp/ax-b-retire-$TAG-$ROLE/`，原数据盘仅在私有命名空间用 `ro,noload` 挂载，验证后卸载。原 30 条路径任何一条缺失，先核独立盘外证据或记缺失原因；不能把缺失当作已归档。只在确有证据时用 `--generated-bucket` 加准确桶名，或用 `--extra-evidence` 将准确的新证据根纳入私有包；两次命令参数相同。没有清点到的工作区、系统盘上的旧日志和无法说明的文件都保留。

在 jump 使用 `/srv/ax-pilot-control/parallel-control-01/ssh.conf` 从各 B 节点走私网取 `evidence.tar.gz` 到协调盘的全新 `0700` 目录。源包 SHA 和归档内逐成员 SHA 均核验；再取本机一份或从**已确认为私有**的独立 OSS 桶独立读回。不能使用当前公开 trace 桶。若没有可用私有 OSS，保存 jump 和本机两份，并在本机运行 `python3 preserve-b-backend.py verify "$ROLE" --tag "$TAG" --archive "$COPY"`。核两份大小、外层 SHA、成员数、内部 manifest，确认 C4 原始记录另在独立路径；完成后按 role 保存非敏感小回执。

**释放参数。** 停止已启用的 B 节点，正常停机并实查 `Stopped / StopCharging`。在持锁状态下用 `DescribeInstances` 按表中精确 ID、`DescribeDisks` 按各实例/盘 ID 核对 region `cn-hangzhou`、zone `cn-hangzhou-j`、campaign `ax-pilot-20261111-b`、角色、按量计费、精确附盘集合及每盘 `DeleteWithInstance=true`。每台单独确认私有盘外归档、未知项为零、已关闭。只对满足该项的原实例发送 `aliyun ecs DeleteInstance --RegionId cn-hangzhou --InstanceId <exact-instance> --Force false`；不使用旧 `cloud.py release` 的 Force 路径。随后按本次释放的实例 ID 和全部关联盘 ID 逐项查 `TotalCount=0`，保存 API 请求号；其余资源原样留存。任何检查不符，停止该角色并保留回执；无自动重试。
