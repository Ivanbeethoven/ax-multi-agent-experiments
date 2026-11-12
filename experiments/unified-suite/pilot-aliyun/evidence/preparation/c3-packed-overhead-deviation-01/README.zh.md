# C3 Packed 观测开销偏离：申请与记录

状态：`reviewed: true`。机器可读版本见同目录 [plan.json](plan.json)（`schema: c3-observer-overhead-deviation-v1`）。

这是一条**显式协议偏离**的申请与记录，不是一次静默放宽。它只覆盖 C3（十万小文件）的 BrewFS Packed 臂、只覆盖**新一批**的一次正式样本；它不把 packed 改成已通过，也不重算、不回填任何已验收单元。

## 1. 一句话结论

C3 packed 的资格、容量、版本和六轮校准都已通过，唯一未过的是观测开销：**5.9836%（makespan）/ 5.9841%（任务 P99）> 冻结门槛 5%**。用户已明确接受「只报告端到端性能、把观测器开销作为已披露的已知偏差记录」。因此本批结果只能表述为**「在 5.98% 观测开销下的端到端性能」**，不能表述为「观测器无影响的性能」。

## 2. 现状与证据

C3 在冻结计划里是 4 个单元（`16f4ac389d2a2da6-r1-{native,juicefs,packed,entity-native}`），当前 **2/4**：BrewFS flat（`-native`）与 JuiceFS flat（`-juicefs`）已验收；packed 卡在观测开销；entity-native 受 native lower 的 32,768 行上限限制（[C4-NEXT.zh.md](../../../C4-NEXT.zh.md) 第 14 行，另见 [22:00 进度](../../progress/parallel-20261111-2200-01/STATUS.zh.md) 的 "另一项"）。全库验收仍为 **14/28**（[22:00 进度](../../progress/parallel-20261111-2200-01/STATUS.zh.md)、[23:24 进度](../../progress/parallel-20261111-2324-01/STATUS.zh.md)）。

| 事项 | 状态 | 证据 |
|---|---|---|
| 完整功能资格（八阶段、原生排空、正常退出、子/父 100,000 文件 409,600,000 B 冷验） | 通过 | [qualification/route-result.json](../packed-live-batch-01/qualification/route-result.json)（`passed: true`）、[qualification/filesystem.json](../packed-live-batch-01/qualification/filesystem.json)（`passed: true`） |
| 容量（四盘别名同设备、20% 余量、再留七份） | 通过 | [capacity/result.json](../packed-live-batch-01/capacity/result.json)（`passed: true`） |
| 版本（分支最新：brewfs `ca07159…`、rustfs `f4ed2025…`） | 通过 | [launch/latest.json](../packed-live-batch-01/launch/latest.json)（`passed: true`） |
| 六轮校准（每轮八阶段、8 份 worker CSV、256 修改文件检查、两次原生零债务、20,224 逻辑读 / 768 逻辑写、读 82,837,504 B / 写 3,145,728 B） | 六轮全清 | [packed-calibration-a-01/README.md](../../progress/packed-calibration-a-01/README.md) |
| 观测开销门槛（`max_relative_change = 0.05`） | **未过：0.0598** | [calibration-overhead.json](../../progress/packed-calibration-a-01/calibration-overhead.json)（`passed: false`） |
| 正式 packed 样本 | 未启动（`pending`，`formal_stages_started: 0`） | [handoff-calibration-only.json](../../progress/packed-calibration-a-01/handoff-calibration-only.json) |

流水线确实停在 `pairs` 阶段：检查器写出结果后按 `return 0 if result['passed'] else 1` 返回 1（`scripts/check-packed-c3-live-calibration.py:106`），实际退出记在 [checker-exit.json](../../progress/packed-calibration-a-01/checker-exit.json)（`exit_code: 1`）与 [pipeline-failure.json](../../progress/packed-calibration-a-01/pipeline-failure.json)（`stage: pairs`, `automatic_retry: false`）。没有自动重跑，也没有领取正式 lease。

## 3. 5.98% 的确切出处

实测值来自 `evidence/progress/packed-calibration-a-01/calibration-overhead.json`，字段路径与值：

| 字段 | 值 | 含义 |
|---|---|---|
| `passed` | `false` | 门槛未过 |
| `relative_changes.makespan` | `0.05983592434606022` | 三对配对相对差取中位数后的绝对值 → **5.9836%** |
| `relative_changes.task_p99` | `0.05984068070127868` | 同上 → **5.9841%** |
| `max_relative_change` | `0.05` | 该次校验收到的冻结门槛 |
| `paired_relative_changes.makespan` | `-0.06207100389622955`, `0.004178209975826297`, `-0.05983592434606022` | 逐对：−6.2071%、+0.4178%、−5.9836% |

六轮绝对值（`trial_details`）：Off1 101.736882757 s / On1 95.421972311 s；Off2 100.509033397 s / On2 100.928981243 s；Off3 101.43564996 s / On3 95.366154083 s。

**取证说明**：本目录所在的仓库副本中，该文件的 SHA256 为 `460b6294c7fea61c09d5d5638cd05b82c16fab2758e4f68fbe0bf6862d78a1dd`，与 [handoff-calibration-only.json](../../progress/packed-calibration-a-01/handoff-calibration-only.json) 里 `overhead` 引用的源文件（`/srv/ax-pilot-control/parallel-control-01/packed-c3-next-03/pairs/overhead/result.json`）SHA256 **逐字节相同**。交叉副本：[packed-cold-phase-diagnosis-02/diagnosis.json](../../progress/packed-cold-phase-diagnosis-02/diagnosis.json) 用同一 SHA 记录同一组数（`original_threshold: 0.05`、`original_gate_passed: false`）。

## 4. 门槛的出处与判据

- **计划侧**：冻结计划 [evidence/preparation/parallel-control-01/plan.json](../parallel-control-01/plan.json) 的 `config.telemetry.max_relative_overhead = 0.05`（本地副本第 37–41 行），packed 单元 `16f4ac389d2a2da6-r1-packed` 的 `required_gates` 含 `complete-telemetry-and-overhead`（第 2525–2534 行）。该文件副本 SHA256 `8649d8b77163e9c47de5a5df2a8f8b2a633e305ec4f657c7ba8115a3e02a76e7`，与 JuiceFS C3 保留回执里 `original_plan` 的源文件 SHA 一致；其内嵌 `plan_sha256 = 0feb04c5…` 与各 cell 关闭回执引用的计划哈希一致。
- **判据**：`bench/runtime/observe.py` 的 `overhead()`（第 292–329 行）。要点：
  - 至少 3 对 on/off；`max_relative_change` 必须落在 `(0, 0.05]`，否则直接 `raise`（第 295–296 行）；
  - 每对必须通过自身负载与实时遥测，且 `purpose` 必须是 `observer-calibration-on/off`；
  - 逐对比较工作量签名（`smallfiles` 取 `files`、`verified_reads`、`writes`、`actual_read_bytes`、`actual_write_bytes`），**工作量不同就拒绝**（第 311–322 行）；
  - `changes = abs(median(paired on/off relative change))`，`makespan` 与 `task_p99` 两个指标都要 `<= max_relative_change` 才算 `passed`（第 323–326 行）。
  - 因此「5.98%」是**配对相对差中位数的绝对值**，不是三对共同偏移：逐对为 −6.21%、+0.42%、−5.98%，方向是 On 更快（负号）。
- 该门槛**无法用参数绕过**：`bench/suite_plan.py:81-83` 要求 `telemetry` 恰好等于 `{interval_seconds:5, max_gap_seconds:30, max_relative_overhead:0.05}`；`bench/suite_report.py:41-43` 又要求样本内 `observer_overhead` 的 `max_relative_change` 不超过计划值。所以本条偏离只能在**批次层显式记录并披露**，不能靠改计划字段实现——这也正是它必须写成申请而不是改数字的原因。

## 5. 这个门槛原本保护什么

它保护的是一句可归因性主张：**观察者不扭曲被测负载**。

只有配对 on/off 的差落在预设范围内，才能说「测到的端到端耗时差异来自被观测系统，而不是观测器」。这条主张是所有跨臂比较（BrewFS flat / packed、JuiceFS flat）可以并列的前提；一旦观测器本身带来未知量级的扰动，同一张表里的两个数字就不再共享同一个"干净"前提。判据选 `makespan` 与 `task_p99` 两个指标、用配对而不是同挂载对比（`overhead()` 的 docstring 明确写了 "not a same-mount strict causal claim"），就是为了让"观测器是否改变负载"成为可证伪的门槛，而不是事后解释。

注意这里的偏差方向：三对里两对 On（开采集）**更快**，且较快的两轮恰好在新启 collector 之后运行（[校准留档 README](../../progress/packed-calibration-a-01/README.md)）。现有证据只把差异定位到 `cold-read` 阶段，**不能**归因于 collector、缓存或对象后端。所以本偏离接受的是"6% 量级的未解释配对差 + 只报端到端性能"，**不是**"已证明观测器只贵 5.98%"。

## 6. 用户决定

用户已明确表示：**可以只观测端到端性能，不介意观测开销超过 5%**。

据此，本批的做法是：

- 门槛不通过这一事实**原样保留**在 `calibration-overhead.json`（`passed: false`）里，不改写、不重算、不删除；
- 观测器开销作为**已披露的已知偏差**进入结果文本与回执，而不是隐藏；
- 正式样本只报告端到端性能，不额外声称观测器无影响；
- 5% 之外的所有判据一律不变（见第 8 节）。

## 7. 偏离后的声明边界

**可以**这样表述（必须带上偏差与逐对范围）：

> 在 5.98% 观测开销（三对配对相对差中位数绝对值；逐对 −6.21% / +0.42% / −5.98%，方向为开采集更快，差异集中在 cold-read，未归因）下，C3 packed 单轮端到端 makespan 为 ⟨实测⟩ 秒。

**不可以**这样表述：

1. 「观测器无影响的性能」「零开销」「干净基线」——观测器确实带来 6% 量级的未解释配对差；
2. 「与已有 flat 结果同等可比」——flat BrewFS 臂的三对校准是 2.85% / 2.86%（[observer-overhead.json](../../c3-flat-01/observer-overhead.json)，`passed: true`），JuiceFS 臂另有自己的校准；本臂 5.98%。跨臂比较**必须同时给出两臂各自的偏差**，否则等同于把不同前提的数字并排放；
3. 拿 5.98% 当校正系数去折算数字（例如除以 1.0598）：方向为负、且只定位到 `cold-read`，不是可外推的减速比例；
4. 把本条用于其他臂或其他 C3 单元（`applies_to` 只含 `16f4ac389d2a2da6-r1-packed`）。

## 8. 这属于新一批的协议偏离

- 只对**新一批**的 C3 packed 一次正式样本有效；旧批次（含这六轮校准）保持原判 `passed: false`。
- **不改写已验收的 14/28**：C3 分母仍是 4，本条不把 packed 记为已通过；只有新一批按第 7 节边界出具结果后，才作为新一批单独记账。
- **不回填旧批次**：已验收的 [C3 BrewFS flat](../../c3-flat-01/accepted.json)（`16f4ac389d2a2da6-r1-native`，`accepted: true`）与 [C3 JuiceFS](../../c3-jfs-01/formal/sample.json)（`16f4ac389d2a2da6-r1-juicefs`，独立收尾通过）连同其余 12 项保持原样，不因本条重算或改判。

## 9. 仍需满足的其它条件（5% 之外的判据全部不变）

新一批 packed 正式样本除了本条披露之外，仍须满足：

1. **九个原始阶段全跑完**：`observe-start → create-w01 → mount-w01 → measure-w01 → drain-w01 → unmount-w01 → verify-w01 → observe-stop → archive`。
2. **完整冷读回 / CRC 与内容核验**：`verify-w01` 重挂后全量内容核验；资格层的子、父冷验（`cold_child` / `cold_parent`，各 100,000 文件 / 409,600,000 B）不得复用为正式样本的冷验，正式格另做完整冷验。
3. **工作量一致**：与校准同一 workload 合同（`workload_contract_sha256`）、`metric_contract_sha256` 不变；`work-check` 仍需 `passed: true`。
4. **排空与真实退出**：`upload_before_commit+fsync+remote-drain`，`pending_upload_bytes / operations = 0`，进程真实退出、挂载消失、Agent 槽位空。
5. **采样完整性不变**：间隔 5 s、最大间隔 30 s、四类 phase marks 齐备；只放宽"允许的相对差"这一条，采样参数不动。
6. **其余 `required_gates` 不变**：`exact-build-input-inventory`、`native-route-and-metadata`、`effective-cache-and-durability`、`host-capacity-and-maintenance`、`full-base-validation-once`、`client-cache-evidence`、`cross-pool-hardware-equivalence`。
7. **原始归档与可复核**：三角色原始包保留、成员 SHA 可核，本条 `plan.json` 随样本一起被引用。

## 10. 本记录当前还不能自动解锁正式阶段

这一节是本记录的诚实边界：**写下偏离不等于流水线接受偏离**。按当前代码，正式 packed 仍无法被 admit：

| 阻断点 | 现状 |
|---|---|
| `scripts/packed-c3-controller.py:439-442`（`admit_locked`） | 要求 `checked['passed'] is True` 且 `relative_changes` 全部 `<= .05`，否则抛 `Three exact original calibration pairs within five percent required` |
| `scripts/packed-c3-controller.py:279` | 正式样本把 `observer_overhead` 写成 `equals={'passed': True}`，`passed: false` 会让样本验收失败（`bench/suite_report.py:34`） |
| `scripts/packed-c3-controller.py:490-513`（`pairs` 动作） | 三对不通过即 `RuntimeError`，`pipeline-failure.json` 记录的就是这一次 |
| `bench/runtime/observe.py:295-296` | 检查器本身拒绝 `max_relative_change > 0.05`，无法靠改容差重跑 |
| `bench/suite_plan.py:81-83` | 计划里的 `telemetry` 被钉死为 0.05，不能改字段 |

所以要让这批**合规开跑**，还缺一项：**admission / 样本侧的显式消费点**——在 `admit_locked` 与样本 `observer_overhead` 检查里，把"`passed: true`"替换为"要么 `passed: true`，要么携带 `c3-observer-overhead-deviation-v1` 的 `reviewed: true` 引用并满足 `disclosure` 契约"，并把该引用写进 `context.json` 与最终结果文本。这属于代码与新批次请求的改动，本记录只提供被判定的依据。

## 11. 取证清单（本地副本 SHA256）

| 文件 | SHA256 | 说明 |
|---|---|---|
| [calibration-overhead.json](../../progress/packed-calibration-a-01/calibration-overhead.json) | `460b6294…d78a1dd` | 5.98% 的原始结果，与源文件逐字节相同 |
| [packed-calibration-a-01/README.md](../../progress/packed-calibration-a-01/README.md) | `9e800cd0…7cb16ec` | 六轮与逐对差值、方向说明 |
| [handoff-calibration-only.json](../../progress/packed-calibration-a-01/handoff-calibration-only.json) | `b0d9a70a…3e8de` | `formal_packed_state: pending`、`formal_stages_started: 0` |
| [checker-exit.json](../../progress/packed-calibration-a-01/checker-exit.json) | `56296949…8e19027` | `exit_code: 1` |
| [pipeline-failure.json](../../progress/packed-calibration-a-01/pipeline-failure.json) | `d85e6262…f28ec67` | `stage: pairs`，不自动重试；与源 `failed.json` 逐字节相同 |
| [packed-cold-phase-diagnosis-02/diagnosis.json](../../progress/packed-cold-phase-diagnosis-02/diagnosis.json) | `15f91d64…51978b` | 同组数的只读诊断，明确"不接受该门槛" |
| [plan.json（冻结计划副本）](../parallel-control-01/plan.json) | `8649d8b7…2a76e7` | `max_relative_overhead: 0.05` |
| [qualification/filesystem.json](../packed-live-batch-01/qualification/filesystem.json) | `5d447de6…4297d1` | 与 `calibration-overhead.json` 的 `reused_full_cold_qualification` 同源 |
| [qualification/route-result.json](../packed-live-batch-01/qualification/route-result.json) | `19dbf93a…a309dd` | 完整资格 `passed: true` |
| [capacity/result.json](../packed-live-batch-01/capacity/result.json) | `24d56451…46eba5` | 容量 `passed: true` |
| [c3-flat-01/observer-overhead.json](../../c3-flat-01/observer-overhead.json) | `6103f6eb…9e88be` | flat 臂 2.85% / 2.86% |
| `bench/runtime/observe.py` | `29bdf9cd…a2efd` | `overhead()` 判据 |
| `bench/suite_plan.py` | `f62e8e52…21435` | telemetry 钉死 0.05 |
| `bench/suite_report.py` | `f217453a…b1625` | 样本侧 overhead 比较 |
| `scripts/packed-c3-controller.py` | `59e1a403…a4b5c` | admit / pairs 的硬判据 |

## 12. 未找到 / 未覆盖

- **未找到原始回执**？不需要这样写：5.98% 的原始回执**已找到**，即上表第一行，且仓库副本与源文件 SHA 逐字节相同。本记录不存在"找不到出处"的情况。
- 未找到：该次 overhead 检查的**计划文件本体**（`.../pairs/overhead/plan.json`）未随结果归档到仓库；`calibration-overhead.json` 内嵌了 `pairs`、`max_relative_change` 与两个合同 SHA，字段上足够独立重算，但重算还需要控制机上六份 `cohort/result.json`，本地不具备。
- 未覆盖：collector 的 CPU/IO 影响量级、缓存状态归因——现有证据只能定位到 `cold-read` 阶段的差值，不能归因（同 [packed-calibration-a-01/README.md](../../progress/packed-calibration-a-01/README.md) 与 [diagnosis.json](../../progress/packed-cold-phase-diagnosis-02/diagnosis.json) 的口径）。
