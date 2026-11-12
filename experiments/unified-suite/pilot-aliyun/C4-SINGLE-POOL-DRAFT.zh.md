# C4 单池：用 A 的存储与 A、B 两台客户端

可以生成独立计划。一个新的逻辑池 `pool-c4-handoff` 包含 A client（源）、B client（目标）、A Redis 和 A RustFS。A、B client 各用本机的 `/sys/fs/cgroup/ax-slot-1`，合计两个槽。B 的 Redis、RustFS 不需要启动。原双池计划、资源清单和状态库照旧保留。

在仓库根目录生成离线草稿：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/draft-c4-single-pool.py \
  --code-directory code-12 --out /tmp/c4-single-pool-draft-01
```

输出 `inventory.json`、`config.json`、`plan.json` 和 `draft.json`。原 `suite_plan.build` 生成 `handoff-1` 四个历史单元（BrewFS 与 JuiceFS 各自 stay/move），放进同一个配对块；输入仍为 `archive-io-20261026`。输入和新构建清单尚未绑定，脚本不探测、不创建状态库，也不签任何资格。旧计划的验收数不因这份草稿变化。

原 `setup-controller.py` 可为这个清单分配四个节点和两个执行槽，但它会实际 SSH 探测。目标 B 以前用 `/srv/ax-client`；新角色要求真实的 `/srv/ax-target/code-NN` 和该路径下的可用数据盘，符号链接无法通过探针。执行窗口应先确认独立路径在 B 的原数据盘上并留足容量，不改旧目录；重新核查 FUSE、运行代码、两个槽和主机身份。计划的正式观测要求覆盖四个节点，所以 stay 的目标虽不运行任务，正式测量仍需保留目标的主机观测。

新池在原协调者 `runner-locks` 下占 12 把锁：新池名 1 把、4 台主机与 7 块数据盘各 1 把；与旧 A 共有 9 把，与旧 B 共有 2 把。必须先确认两个旧池都交出了资源，运行窗口内阻止旧任务回占 A 的三台机器或 B client。所有进程使用同一物理锁目录，不能把锁移到新状态库旁另建一份；同一物理锁不能在同一进程里重复获取。持锁时还要查各状态库：旧并行计划记录 `cell_leases/cell_resources`，新 `paired-pool` 记录 `blocks/resources`，仅调用现有 `active_conflicts` 会漏掉后者。

执行仍缺正式接线：`run-c4.py` 只接受 fork；`run-c4-native-handoff.py` 和其请求工具强制源、目标分属两个逻辑池；模型免调用的状态恢复工具只认 `opencode-checkpoint-20261111`，不是该单元。底层 `bench.runtime.handoff` 按节点而非逻辑池判定 stay/move，可在新接线里保留原校验。四个单元都要先完成资格和输入准备才可认领此配对块；确认两种文件系统各自的共享卷、挂载、写者交接、五个阶段记录和收尾后，才能测量。新计划需单独保留结果和成本；仅有同名 cell ID 不构成旧计划的证据。

已用仓库保存的实际主机和盘 ID [生成四份草稿](evidence/preparation/c4-single-pool-draft-01/draft.json)；[原规划器输出](evidence/preparation/c4-single-pool-draft-01/plan.json)恰有四个 handoff-1 单元。没有部署、签署资格或使用模型。
