# 历史 C4 On1 离线恢复结果

用户确认后，协调者在 A 池物理锁内执行一次恢复（11:25–11:39），[退出码为 0](recovery-run.exit.json)。原试验及 continuation 不写入、不删除；原 `trial/exit.json` 仍为 1，没有新增原目录的 `finished.json`。 [审阅件](recovery-request.reviewed.json)只把原草案的 `reviewed` 改为 true，[确认记录](recovery-approval.json)固定草案、审阅件和工具的 SHA-256。

- [恢复回执](recovery.json)证明三份归档按原格式通过逐成员核验，client / metadata / objects 分别有 **3,060 / 3,073 / 2,954** 个观测样本。每个角色都通过 30 秒最大间隔和 `measure-start → measure-end → drain-end → verify-end` 四个标记。
- 原计时任务的 [工作检查](work-check.json)、原 `touched-cold-v1` 冷验、正常退出和 [闭合](closure.json)均通过；最终未上传字节和操作数均为零。没有把 touched 冷验改称全量冷验。
- [恢复后的校准回执](finished.json)与 [输出回读](recovery-run.stdout)的 SHA-256 一致。数据在协调者 `/srv/ax-pilot-control/preparation/a-c4-on1-historical-archive-01/recovered-trial`；外部只同步小回执，未迁移归档和原始样本。

这是旧观察者顺序的第二轮（Off1 → On1）；后面仍缺 On2、Off2、Off3、On3，须先补新 boot 的容量和运行准入，再跑原 5% 检查。不能混入直接写 NDJSON 的新观察者校准。正式验收仍为 **14/28**，无新测量、无新计算实例。

[首对诊断](off1-on1-diagnostic.json)：同一工作签名、同一指标与负载合同下，Off1 makespan 8,453 秒，On1 15,330 秒，On1 约慢 81.3%；两个单任务时长的 P99 变化接近。仅此一对，未运行原三对差值中位数的 5% 门槛，不能把 81.3% 当作采集器因果开销。

后续脚本已在原协调者的独立代码目录只读核对[历史续跑前缀](prefix-readback.json)。固定旧 Off1 全量参考、恢复后的 On1 和同一 legacy-fold 读取器，在 A 池锁内检查 5 个控制库。未核对当前容量，也未派发 worker；旧请求仍不能越过停机后的 boot/PID 变化。
