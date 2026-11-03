# AI 逐步维护与交接

## 协调者任务单

> 先读本目录README、方法、baseline/消融、环境、输入、执行、12-parallel-baselines和证据文档，再读本批plan、资源账本、state status和最近原始日志。外部baseline仅JuiceFS，BrewFS native为内部对照。按product_pools/arm_pools把不同baseline拆到不同集群并行，各池领取单cell，跨池按同条件/轮次汇总block；核cross-pool-hardware-equivalence。核build/input/inventory及真实容量，逐池安排准备，禁止与同池或共享资源测量重叠。只在一个协调者本地DB领取，不复制DB绕锁。共享host/disk/backend使用相同resource ID。每步核原started/PID/输出，不重放已开始的导入/clone/测量。不因慢/断联杀任务、改默认语义、跳校验或新开资源。无变化保持安静，仅通知实质进展、失败、完成或需要人类决定。

## pool执行者任务单

> 接收固定pool、owner和lease路径，读cell和qualification。只执行原身份对应的单步命令，使用新结果路径并绑定精确SHA。严格按cell.stages，C3每个workspace有独立创建/挂载/测量/排空/退出/校验，全部留原始回执。普通卸载/退出、父兄弟隔离与冷读回均核。中断先读原远端PID/boot/starttick、租约和日志，未知不重跑。原阶段全完成才报告complete，接管或gap保留failed/recovered。不修改冻结输入、运行时、验证器或已执行spec。

## 审查者任务单

> 重核计划/资产、归档/成员、原始操作/观测三类摘要，独立核完整block每个arm。确认gap、维护、中断、慢项、未支持消融都有记录。完整base校验一次仍有真实字节验证，touched/父/兄弟/远端校验不可省。只给完整无中断配对结论；纯D/P/G需同引擎、同其他因素，不把系统路径变化写成单因素收益。核准备/等待/归档和创建至实际释放成本，再完成如实报告。

## CURRENT.json

```json
{
  "campaign_id": "actual-campaign",
  "plan": {"path": "/srv/control/plan.json", "sha256": "actual sha256"},
  "state_db": "/srv/control/state.sqlite",
  "resource_ledger": "/srv/control/resources.json",
  "active_leases": ["/srv/control/lease-a-001.json", "/srv/control/lease-b-001.json"],
  "active_stages": {"pool-a": "measure", "pool-b": "create"},
  "original_process_receipts": ["/srv/control/cell/process.json"],
  "latest_evidence": ["/srv/control/cell/started.json"],
  "unresolved": [],
  "next_single_action": "inspect original process and stage output",
  "notification_policy": "meaningful change, failure, completion or required decision only"
}
```

这是形状示例，实际文件不能保留占位。state为事实源，交接文本不重置执行状态。资格失败写缺项及具体输出，不能只写“都准备好了”。当前恢复正常也保留原stderr。

| 异常 | 下一步与结论 |
|---|---|
| claim拿不到cell | 看资格/asset/共享资源/原owner，不缩测试、不另造DB绕锁 |
| collector重启/gap>30s | 恢复采集、保留gap与维护日志，完整观测验收失败，已开始工作不重放 |
| controller退出而clone活着 | 核原身份，等待/接管确切组件，标恢复样本 |
| partial gzip/tar | 保留interrupted原件，确认无人写，再从真实源恢复采集，完整前不释放 |
| hint/cache未实际生效 | 核路径/请求/对象布局，记录no-effect/blocked，不暗换binary |
| stage结束但state仍started | 核finished与全部子进程，保存诊断/恢复回执，不设回pending |
| 遍历/债务长尾 | 看进度、盘、RPC、pending，全部时间/费用保留，不省检查 |
| 失败但已封存/退出 | failed收尾，按账本释放已授权自有资源，不伪造成功 |

开发变更先做 [本地验证](../../tests/README.md) 与test_unified_suite.py。小样本不是百万性能测试，新功能需新campaign正式资格，不污染已封存结果。
