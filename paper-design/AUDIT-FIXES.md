# 审计修复记录（2026-10-28）

依据：`paper-design/REPO-AUDIT.md` 与 `/home/hxy/REPO-AUDIT.md` 的完整审计内容。此次只改 `paper-design/` 文档、JSON/CSV 计划模板和 `paper-design/tools/` 校验脚本；`experiments/`、`results/`、`validation/` 既有证据未改、未删。

## Blocker

| 项目 | 做了什么 | 依据 | 未做及原因 |
|---|---|---|---|
| B1 本批 pin | 新建 [`BACKEND-PIN-20261028.json`](BACKEND-PIN-20261028.json)，记录真实 `git ls-remote` 原始输出、UTC 解析时间、RustFS branch/SHA、镜像 digest、revision label、BrewFS commit/二进制 SHA、JuiceFS/fio 版本和来源。候选镜像实际 `docker inspect` label 与 `frozen_commit` 相等。 | `AGENTS.md` 的逐批 pin 合同；现场命令输出为 `a87e8f94a50b9fb6e3da74fed45fe37d4cb969ed refs/heads/feat/dynamic-block-size-rebased`。 | 未添加 `blocked_reason`，因为现场 head 与要求的冻结 SHA 一致且镜像 label 通过机械核对。 |
| B2 资格失败与无有效记录 | 新建 [`EXECUTION-20261028.md`](EXECUTION-20261028.md)，把 C4 G1 失败、停止规则、四组状态和 C5 外部基线分开写明；明确覆盖度为 0/50，不把失败批次当论文结果。 | C4 `REPORT.md` 的三次挂载失败和审计的“无一格有效结果”结论。 | 没有修改或伪造实验数据，也没有宣称资格已通过；真正修复客户端/catalog 后必须另开新批次，超出本次文档任务和只读证据约束。 |
| B3 动态块 no-op | `run-grid.json` 的三个 `object_granularity` cell 增加 `b_hint_bytes=[262144,4194304]`，judge 明确 B 必须显式取 256 KiB 或 4 MiB 才产生 `ec-read-quantum`，默认 1 MiB 判 `unmeasured(默认 B 使特性成为 no-op)`；`check_design.py` 让展开 CSV 对应行写入同一字段。 | `EXPERIMENT-CONFIG.zh.md` 与审计指出默认 B=1 MiB 时 `frame_size==shard_size`。 | 未补运行时 B/q 或布局 digest 数字，因为本批没有有效 object-granularity 产物。 |
| B4 两个机器可读源 | 新建 [`experiment_class_registry.json`](experiment_class_registry.json)，`design.json`、`execution-grid.json`、`run-grid.json` 均引用同一注册表；注册表保留 design 的 11 个详细类，并登记 run-grid 的 14 个 canonical ID，额外 `attribute-cache`、`reopen-cache`、`write-cost` 标为 planned 且写明“参数未冻结，来自 2026-10-28 实测补充”。校验器检查三者引用和类集合。 | 审计的 11/14 类冲突。选择“统一 registry”是因为 execution-grid 的 35 组仍需保持原有冻结详细协议，三类实测补充不能假装已有冻结参数。 | 未把三类补成 execution-grid 的冻结组，也未开启执行；它们仍是 planned 补充。 |
| B5 记录模板无法绑定 cell | 扩充 [`run-record.template.json`](run-record.template.json)：加入 `cell_id`、`layer`、`judge`、`expected_artifact`、`config`、`workload`、`params`、`reps`、`status`、`artifact_paths`、`measured_at`、`host`、`device_map`；device_map 分开记录 corpus、fio target、cache-dir 和 4 个 volume 的实际设备。校验器检查字段和层枚举。 | 审计模板字段缺口和 `TEST-PLAN` 的逐格产物合同。 | 模板仍是未执行占位，状态为 `unmeasured(template-only)`；没有填任何实测结果。 |
| B6 `plan.zh.md` 死链 | 新建 [`plan.zh.md`](plan.zh.md)，只做稳定入口，指向 `design.json`、`TEST-PLAN.zh.md`、`EXPERIMENT-CONFIG.zh.md` 和 registry；更新 TEST-PLAN/README 的入口和链接。 | 选择审计建议 (a)，避免多份文档各自复制参数；现行权威文件由链接直接指定。 | 未删除历史文档或历史副本。 |

## Major

| 项目 | 做了什么 | 依据 | 未做及原因 |
|---|---|---|---|
| M1 W1-W4 与 50 格 | 新建 `EXECUTION-20261028.md`，逐组映射到最近 cell 或 `no-cell`，列出 0/50 严格覆盖、C4/C5 节点路径、归档 SHA-256、ok/void/unmeasured 分布和三项已知缺陷。 | 审计第 4 节及外部 C4/C5 manifest/report。 | 没有把 W1 的类别近似或 W3/W4 的部分参数匹配夸大成有效覆盖。 |
| M2 单机不可行格 | `migration-*`、`scale-*`、`recovery-*`、`agent-*` cell 加 `single_node_ok:false` 与 `required_conditions`；条件写明多节点/worker placement、故障注入或真实会话要求。 | 审计逐格表的“需要多节点/缺条件”结论。 | `replay-*` 未标 false，因为 4 worker 回放可在计划资源内运行；其 trace 资格仍由原 judge 负责。 |
| M3 layer 归属 | `write-flat`、`write-overlay`、`write-packed`、`write-juicefs` 从 `backend` 改为 `disk`，并用 `measurement_layers` 把 application/readback、remote/drain、device bytes 分别归到 client/backend/disk；`object-client-g` 从 `backend` 改为 `client`，补充 g/object/read 请求与 backend service bytes 的分层。 | 审计指出四个 write cell 把 device bytes 混入 backend，且 g 是客户端对象形成。 | 未拆成多行 cell；通过主 layer 加逐指标 `measurement_layers` 保持 50 格索引稳定。 |
| M4 9 格阈值 | `packed_access` 三格加入 probe completion≥99.5%、p95≤2s、错误率≤0.5%；`packed_lifecycle` 三格加入 100% readback、错误率 0、观察 K 的累计成本交叉和 p99≤2x；`agent_validation` 三格加入 score≥0.80、文件状态 100%、至少一次真实后续 tool call、错误率 0。另写入结构化 `judge_thresholds`。 | 审计指出原 judge 只有联合判断/观察交叉点/score。 | 未把阈值用于历史结果重判；历史结果仍按原批次状态保留。 |
| M5 装置硬约束入口 | TEST-PLAN 增加 `EXPERIMENT-CONFIG.zh.md` 与 `tools/check-disk-layout.sh` 链接，并明确脚本退出码是四盘/裸盘落点资格门；README 增加 REORG 与 JUICEFS 对照入口。 | 审计一致性检查第 5 条及孤儿文件问题。 | 未执行云端磁盘检查，因本次明确不启动云实例。 |

## Minor

| 项目 | 做了什么 | 未做及原因 |
|---|---|---|
| 孤儿入口 | README/TEST-PLAN 增加 `REORG-EXPERIMENT-PLAN.zh.md`、`JUICEFS-PARITY.md` 链接；不删除文件。 | 已完成。 |
| CSV 行数歧义 | README 明确 `run-grid.csv` 的 1174 条展开行不等于 1174 个 cell，50 个 cell 摘要在 `run-grid.json`。 | 已完成。 |
| 绝对路径复现 | `EXECUTION-20261028.md` 同时记录节点路径、解析路径和 c4/c5 归档 SHA-256；后续跨主机记录沿用该双记录规则。 | 未大范围改写历史文档中的外部输入路径，以免改变历史证据语义。 |
| 历史版本口径 | `EXECUTION-20261028.md` 将 C5 明确标为 JuiceFS 外部基线，将 C4 标为失败资格批次，不与计划结果混列。 | 根目录历史材料不属于本次允许修改范围。 |

## 校验

```text
python3 paper-design/tools/check_design.py
```

通过；校验仍保持 `execution_enabled=false`，只检查设计一致性，不把任何实验产物标为有效结果。
