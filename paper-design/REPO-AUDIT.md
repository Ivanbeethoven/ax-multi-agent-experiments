# ax-multi-agent-experiments 仓库审计报告

审计日期：2026-10-28
审计提交：`7b85e43`（工作树起始状态干净）

## 一句话结论

**不能把当前仓库作为一批实验的唯一依据。**它能作为计划和 50 格索引，但执行仍被 `design.json` 的 `execution_enabled=false`、缺失的 `paper-design/plan.zh.md`、运行记录与格子未绑定、以及本批 BrewFS 配置资格失败阻断；当前没有一格可被仓库内证据认定为有效结果。

## 1. 事实基线

审计命令得到：

- `git log --oneline -8` 首行为 `7b85e43`，前七个历史提交依次包含机器/磁盘约束、方案重写、JuiceFS 全覆盖、计划重整、归档修复和仓库整理。
- `git ls-files | wc -l`：`1558`；`find` 排除 `.git` 后文件数：`1557`。
- 顶层目录体积：`results/154M`、`experiments/57M`、`validation/1.4M`、`paper-design/1.1M`、`inputs/760K`、`scenarios/448K`、`scripts/240K`、`report/184K`、`harness/160K`、`tests/124K`、`infra/96K`、`tools/44K`、`configs/32K`、`skills/272K`、`paper/4K`。
- `python3 paper-design/tools/check_design.py` 通过，但它只证明计划一致性：`execution_enabled=false`、11 个 `design.json` 实验、35 组、50 个 `run-grid.json` cell 展开为 1174 条计划记录；不证明运行资格或运行结果。

### 仓库结构地图

- `paper-design/`：当前实验设计、机器可读网格、输入清单、执行模板和资格工具。
- `paper-design/reference/`：按实验主题展开的操作、计时、统计和边界细节。
- `paper-design/tools/`：设计校验器和磁盘落点检查脚本。
- `paper-design/validation/`：设计审查和生成网格的历史校验结果。
- `experiments/`：历史实验、云上 pilot、负载观察和执行相关材料。
- `results/`：已归档的回放/采集结果；没有本批 2026-10-28 四配置结果。
- `validation/`：仓库迁移及输入/工具验证资料。
- `inputs/`：任务评分输入和 fixture 相关输入。
- `scenarios/`：容器部署、数据目录、执行和观测说明。
- `scripts/`：归档下载、上传、回放和辅助脚本。
- `report/`：独立报告材料和图表入口。
- `harness/`：实验准备、资格和回放执行框架。
- `tests/`：上述 harness 的单元/集成测试。
- `tools/`：通用 FUSE 回放和其他工具。
- `infra/`：阿里云部署和基础设施入口。
- `configs/`：适配器及运行配置。
- `skills/`：实验相关 Codex/Agent skill 资料。
- `paper/`：论文仓库 submodule 入口。

### `paper-design/` 文件作用和权威性

| 文件 | 一句话作用 | 权威位置/状态 |
|---|---|---|
| `design.json` | 冻结 RQ、三种 BrewFS 配置、资源、输入和 11 类主实验的机器可读设计。 | **最高参数权威**；当前 `planned`，`execution_enabled=false`。其中 `observed_commit` 只是观测，`frozen_commit=null`。 |
| `plan.zh.md` | 预期中的旧/主方案叙述入口。 | **当前仓库缺失**；历史副本在 `experiments/ax-multi-agent/paper-design/plan.zh.md`，不能当作当前文件。 |
| `TEST-PLAN.zh.md` | 2026-10-28 执行前协议，定义资格门、四配置负载、判据、产物和停止规则。 | 执行协议；参数冲突时服从 `design.json`。 |
| `REORG-EXPERIMENT-PLAN.zh.md` | 把旧代号重写为可读实验章节，并补充 TTL、缓存、写入分解、B/q 和 fencing 规则。 | 补充协议；部分内容没有回写 `design.json`。 |
| `run-grid.json` | 50 个面向执行的 cell 摘要，每格含 workload、judge、layer、artifact。 | 生成的执行索引；**不是** `design.json` 的同级冻结参数源。 |
| `run-grid.csv` | 把执行网格展开成配置/轴/重复的计划行。 | 生成物；1174 条计划行，不等于 50 个 cell。 |
| `execution-grid.json` | 35 个 group 的精确轴、tier、资源和重复计划。 | 更细的机器可读参数；应由执行器消费，优先于人工摘要。 |
| `live-tasks.json` | 真实 Agent 验证用的任务、提示词和输入契约。 | Agent 实验输入；依赖真实会话执行。 |
| `first-pass.csv` | 51 行 smoke/pilot 首轮选择。 | 试运行子集，不是正式结果。 |
| `fixtures.json` | F1/F2/M 等 fixture 的来源、摘要和确定性定义。 | 输入身份清单；fixture 本体不在仓库。 |
| `run-record.template.json` | 执行记录的字段模板、版本和观测槽位。 | 计划模板；不能独立绑定 `run-grid` cell。 |
| `JUICEFS-PARITY.md` | 每个实验小节的 JuiceFS 负载、公平条件、无等价能力和不适用指标表。 | 全局对照规则；与 `TEST-PLAN` 互相校验。 |
| `EXPERIMENT-CONFIG.zh.md` | 每批 8 vCPU/30 GiB、4×250 GiB PL1、目录落点、RustFS pin 和裸盘标定硬要求。 | **装置硬约束**；应被 `TEST-PLAN` 明确引用。 |
| `README.md` | 人读执行手册和入口，说明顺序、计时、工具和结果边界。 | 导航/协议摘要；不覆盖 `design.json` 数值。 |
| `reference/` | 细节协议、指标、阶段状态机和统计合同。 | `README` 的深层备查资料。 |

权威顺序建议固定为：用户/`AGENTS.md` 的部署合同 → `design.json` 冻结参数 → `EXPERIMENT-CONFIG.zh.md` 装置硬约束 → `execution-grid.json` 精确轴和组 → `TEST-PLAN.zh.md` 执行资格/判据 → `REORG-EXPERIMENT-PLAN.zh.md` 补充解释 → `run-grid.json`/`.csv` 生成索引 → `JUICEFS-PARITY.md` 对照规则 → `reference/` 和 `README.md` 操作细节 → `first-pass.csv`、`fixtures.json`、`live-tasks.json`、`run-record.template.json` 输入/记录模板。`plan.zh.md` 缺失，不能放进权威链。

## 2. 设计之间的矛盾和覆盖关系

- `design.json` 只列 11 类实验；`run-grid.json` 实际有 14 类，额外出现 `attribute-cache`、`reopen-cache`、`write-cost`。这三类在 `design.json` 没有冻结参数，必须补入设计或明确它们是执行前扩展，不能把 50 格全部称为同一冻结方案。
- `REORG-EXPERIMENT-PLAN.zh.md` 的 Overlay TTL 文字是 `0/100 ms/1 s`；`TEST-PLAN.zh.md` 和 `run-grid.json` 实际是 `0/1000 ms` 加默认有效 TTL。按用户要求，涉及冻结参数时以 `design.json` 为准；但这些三类实验没有对应 `design.json` 字段，当前无法裁决，建议统一成一个机器可读来源。
- `design.json` 配置名是 `native`、`native-overlay`、`native-overlay-pack`；`run-grid.json` 写成 `BrewFS flat-v1`、`BrewFS Overlay + native lower`、`BrewFS Overlay + Packed lower`；`TEST-PLAN`/`JUICEFS-PARITY` 又写中文全称。语义可映射，字符串不能直接 join。
- `design.json` 的 RustFS `frozen_commit`、image reference/digest 和 measured results 都是空值；这符合“计划未执行”，但意味着不能从该文件推出任何后端已 pin 或动态布局已生效。
- `TEST-PLAN` 明确引用不存在的 `paper-design/plan.zh.md`，同时把 `/home/hxy/us-artifacts/*.tgz` 当实测依据；这两个路径都不属于仓库可复现输入。

## 3. 50 格逐格审计

判定说明：`本机`表示负载拓扑和容量可以放在一台 8 核、30 GiB、4×250 GiB PL1 主机；不代表已通过资格门。`需要多节点`表示网格本身要求多 worker/源目标交接。`缺条件`表示需要仓库之外的真实会话、故障注入或完整 trace。`judge-not-measurable` 只针对 judge 没有给出足以自动判定比较结论的阈值；该格的 `valid_if` 仍可能能判定是否完成。layer 的 PASS 是主测量层；FAIL 表示字段混合了另一层或主变量归属错误。所有 50 格都有 JuiceFS 行。

| cell_id | 单机 4 盘可执行性及依据 | 判据 | layer 一致性 | JuiceFS 对照 |
|---|---|---|---|---|
| `qualification-flat` | 四盘单机可跑：POSIX/读回可由本机多个进程完成。 | 自动可判定 | `client`；PASS | 有（BrewFS flat-v1） |
| `qualification-overlay-native` | 四盘单机可跑：POSIX/读回可由本机多个进程完成。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + native lower） |
| `qualification-overlay-packed` | 四盘单机可跑：POSIX/读回可由本机多个进程完成。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + Packed lower） |
| `qualification-juicefs` | 四盘单机可跑：POSIX/读回可由本机多个进程完成。 | 自动可判定 | `client`；PASS | 有（JuiceFS） |
| `attrs-flat-0` | 四盘单机可跑：stat 工作集≤100k inode。 | 自动可判定 | `fuse`；PASS | 有（BrewFS flat-v1） |
| `attrs-overlay-0` | 四盘单机可跑：stat 工作集≤100k inode。 | 自动可判定 | `fuse`；PASS | 有（BrewFS Overlay + native lower） |
| `attrs-overlay-1000` | 四盘单机可跑：stat 工作集≤100k inode。 | 自动可判定 | `fuse`；PASS | 有（BrewFS Overlay + native lower） |
| `attrs-juicefs` | 四盘单机可跑：stat 工作集≤100k inode。 | 自动可判定 | `fuse`；PASS | 有（JuiceFS） |
| `reopen-flat-on` | 四盘单机可跑：单机 open/read/close 回环。 | 自动可判定 | `fuse`；PASS | 有（BrewFS flat-v1） |
| `reopen-flat-off` | 四盘单机可跑：单机 open/read/close 回环。 | 自动可判定 | `fuse`；PASS | 有（BrewFS flat-v1） |
| `reopen-overlay-on` | 四盘单机可跑：单机 open/read/close 回环。 | 自动可判定 | `fuse`；PASS | 有（BrewFS Overlay + native lower） |
| `reopen-overlay-off` | 四盘单机可跑：单机 open/read/close 回环。 | 自动可判定 | `fuse`；PASS | 有（BrewFS Overlay + native lower） |
| `reopen-packed-on` | 四盘单机可跑：单机 open/read/close 回环。 | 自动可判定 | `fuse`；PASS | 有（BrewFS Overlay + Packed lower） |
| `reopen-packed-off` | 四盘单机可跑：单机 open/read/close 回环。 | 自动可判定 | `fuse`；PASS | 有（BrewFS Overlay + Packed lower） |
| `reopen-juicefs` | 四盘单机可跑：单机 open/read/close 回环。 | 自动可判定 | `fuse`；PASS | 有（JuiceFS） |
| `write-flat` | 四盘单机可跑：ABBA/文件写入和远端确认。 | 自动可判定 | `backend`；FAIL（含远端/设备字节，实际是 backend+disk 混合） | 有（BrewFS flat-v1） |
| `write-overlay` | 四盘单机可跑：ABBA/文件写入和远端确认。 | 自动可判定 | `backend`；FAIL（含远端/设备字节，实际是 backend+disk 混合） | 有（BrewFS Overlay + native lower） |
| `write-packed` | 四盘单机可跑：ABBA/文件写入和远端确认。 | 自动可判定 | `backend`；FAIL（含远端/设备字节，实际是 backend+disk 混合） | 有（BrewFS Overlay + Packed lower） |
| `write-juicefs` | 四盘单机可跑：ABBA/文件写入和远端确认。 | 自动可判定 | `backend`；FAIL（含远端/设备字节，实际是 backend+disk 混合） | 有（JuiceFS） |
| `fork-native-1` | 四盘单机可跑：F1约1GiB，子并发≤64。 | 自动可判定 | `backend`；PASS | 有（BrewFS Overlay + native lower） |
| `fork-native-16` | 四盘单机可跑：F1约1GiB，子并发≤64。 | 自动可判定 | `backend`；PASS | 有（BrewFS Overlay + native lower） |
| `fork-native-64` | 四盘单机可跑：F1约1GiB，子并发≤64。 | 自动可判定 | `backend`；PASS | 有（BrewFS Overlay + native lower） |
| `fork-juicefs` | 四盘单机可跑：F1约1GiB，子并发≤64。 | 自动可判定 | `backend`；PASS | 有（JuiceFS） |
| `retention-native-1000` | 四盘单机可跑：1k/10k状态容量可控，10万为扩展。 | 自动可判定 | `backend`；PASS | 有（BrewFS Overlay + native lower） |
| `retention-native-10000` | 四盘单机可跑：1k/10k状态容量可控，10万为扩展。 | 自动可判定 | `backend`；PASS | 有（BrewFS Overlay + native lower） |
| `retention-juicefs` | 四盘单机可跑：1k/10k状态容量可控，10万为扩展。 | 自动可判定 | `backend`；PASS | 有（JuiceFS） |
| `access-native` | 四盘单机可跑：M最大约1,048,576个1KiB文件，需夹具。 | judge-not-measurable | `backend`；PASS | 有（BrewFS Overlay + native lower） |
| `access-packed` | 四盘单机可跑：M最大约1,048,576个1KiB文件，需夹具。 | judge-not-measurable | `backend`；PASS | 有（BrewFS Overlay + Packed lower） |
| `access-juicefs` | 四盘单机可跑：M最大约1,048,576个1KiB文件，需夹具。 | judge-not-measurable | `backend`；PASS | 有（JuiceFS） |
| `lifecycle-native` | 四盘单机可跑：需夹具和最长GC观察窗口。 | judge-not-measurable | `backend`；PASS | 有（BrewFS Overlay + native lower） |
| `lifecycle-packed` | 四盘单机可跑：需夹具和最长GC观察窗口。 | judge-not-measurable | `backend`；PASS | 有（BrewFS Overlay + Packed lower） |
| `lifecycle-juicefs` | 四盘单机可跑：需夹具和最长GC观察窗口。 | judge-not-measurable | `backend`；PASS | 有（JuiceFS） |
| `object-client-g` | 四盘单机可跑*：必须先冻结有效B/q并证明实际布局变化。 | 自动可判定 | `backend`；FAIL（g 是客户端对象形成，主变量不在 backend） | 有（BrewFS flat-v1） |
| `object-backend-bq` | 四盘单机可跑*：必须先冻结有效B/q并证明实际布局变化。 | 自动可判定 | `backend`；PASS | 有（BrewFS flat-v1） |
| `object-juicefs` | 四盘单机可跑*：必须先冻结有效B/q并证明实际布局变化。 | 自动可判定 | `backend`；PASS | 有（JuiceFS） |
| `migration-native` | 需要多节点：source_workers=4、target_workers=4、16 workspaces，需源/目标交接；扩展还要64/64。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + native lower） |
| `migration-packed` | 需要多节点：source_workers=4、target_workers=4、16 workspaces，需源/目标交接；扩展还要64/64。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + Packed lower） |
| `migration-juicefs` | 需要多节点：source_workers=4、target_workers=4、16 workspaces，需源/目标交接；扩展还要64/64。 | 自动可判定 | `client`；PASS | 有（JuiceFS） |
| `recovery-native` | 缺条件：需要故障屏障、协调断连和真实 stale-writer 注入器。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + native lower） |
| `recovery-packed` | 缺条件：需要故障屏障、协调断连和真实 stale-writer 注入器。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + Packed lower） |
| `recovery-juicefs` | 缺条件：需要故障屏障、协调断连和真实 stale-writer 注入器。 | 自动可判定 | `client`；PASS | 有（JuiceFS） |
| `replay-native` | 缺条件：需要七份 trace（含 pytest-full-02）、资格通过和回放执行器。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + native lower） |
| `replay-packed` | 缺条件：需要七份 trace（含 pytest-full-02）、资格通过和回放执行器。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + Packed lower） |
| `replay-juicefs` | 缺条件：需要七份 trace（含 pytest-full-02）、资格通过和回放执行器。 | 自动可判定 | `client`；PASS | 有（JuiceFS） |
| `scale-native` | 需要多节点：workers=[4,16,64,128]，64/128超出单机8核/30GiB计划资源。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + native lower） |
| `scale-packed` | 需要多节点：workers=[4,16,64,128]，64/128超出单机8核/30GiB计划资源。 | 自动可判定 | `client`；PASS | 有（BrewFS Overlay + Packed lower） |
| `scale-juicefs` | 需要多节点：workers=[4,16,64,128]，64/128超出单机8核/30GiB计划资源。 | 自动可判定 | `client`；PASS | 有（JuiceFS） |
| `agent-native` | 缺条件：requires_model_execution=true，需真实会话接口、评分器和后续 tool call。 | judge-not-measurable | `client`；PASS | 有（BrewFS Overlay + native lower） |
| `agent-packed` | 缺条件：requires_model_execution=true，需真实会话接口、评分器和后续 tool call。 | judge-not-measurable | `client`；PASS | 有（BrewFS Overlay + Packed lower） |
| `agent-juicefs` | 缺条件：requires_model_execution=true，需真实会话接口、评分器和后续 tool call。 | judge-not-measurable | `client`；PASS | 有（JuiceFS） |
统计：**本机能跑 35 格，需要多节点 6 格，缺条件 9 格**。单机清单是 `qualification-*` 4 格、`attrs-*` 4 格、`reopen-*` 7 格、`write-*` 4 格、`fork-*` 4 格、`retention-*` 3 格、`access-*` 3 格、`lifecycle-*` 3 格、`object-*` 3 格，共 35 格；多节点为 `migration-*` 3 格和 `scale-*` 3 格；缺条件为 `recovery-*`、`replay-*`、`agent-*` 各 3 格。`object-*` 的星号表示它仍受运行时 B/q/布局资格门约束。

需要优先处理的逐格问题：`write-flat/write-overlay/write-packed/write-juicefs` 的 `layer=backend` 却要求 `device_bytes`，报告必须拆出 `disk` 账本；`object-client-g` 的 `g` 属客户端对象形成，应该改成 `client` 或拆成客户端/后端两行。`access-*`、`lifecycle-*` 的 judge 只有“联合判断/观察交叉点”，没有预注册的自动优劣阈值；`agent-*` 的“score”也没有通过阈值，因此不能由脚本自动产生比较结论。

## 4. 2026-10-28 四组负载与 50 格

### 映射

| 本批负载 | 可能对应的 cell_id | 对齐结论 | 本批证据结论 |
|---|---|---|---|
| 后端带宽 W1（1 MiB write/read、4 KiB randwrite/randread、direct=1） | `write-flat`、`write-overlay`、`write-packed`、`write-juicefs` | 只有“写入成本/后端”这一层级近似；网格 workload 是 ABBA、小文件、大文件、随机覆盖和写入中删除，不是 W1 的 fio 四负载，因此严格不算同一格。 | 外部 `c4/REPORT.md` 的 G1 挂载失败使 W1 全部 `unmeasured(G1挂载失败)`。 |
| 缓存收益 W2（8 GiB 语料、8192 MiB cache、冷/热 read/randread） | **没有严格对应 cell** | 50 格没有独立“热/冷缓存收益”轴；`reopen-*` 只测关闭后第二次读，不能替代 W2。 | 没有可计入的 cell。 |
| 重复打开 W3（4 KiB/64 KiB/1 MiB，200 轮，KEEP_CACHE on/off） | `reopen-flat-on/off`、`reopen-overlay-on/off`、`reopen-packed-on/off`、`reopen-juicefs` | 参数和判据与网格最接近；JuiceFS 是无等价开关的默认缓存。 | 外部报告明确 W3 全部 `unmeasured(G1挂载失败)`。 |
| 元数据 W4（1k/10k/100k inode，各10万 stat，TTL） | `attrs-flat-0`、`attrs-overlay-0`、`attrs-overlay-1000`、`attrs-juicefs` | 只部分对应：本批还测了 packed 配置和更多 TTL 组合，而网格没有 `attrs-overlay-packed`。 | 外部报告明确 W4 全部 `unmeasured(G1挂载失败)`。 |

严格按“负载和参数完全一致且有有效产物”计数：**0/50 有效覆盖，50/50 尚不能作为结果**。按“可在计划层对齐的候选行”计数：W3 的 7 行和 W4 的 4 行，共 11 行被尝试映射；W1 的 4 行只是近似类别，W2 没有对应行。由于 G1 失败，11 行也全部没有有效数据。

### 与 14 类实验的结构差异

本批是四个低层 microbenchmark：带宽、热/冷缓存、重复打开、stat 元数据。`run-grid.json` 的 14 类则把资格、属性缓存、重复打开、写入成本、Overlay fork、retention、Packed access、Packed lifecycle、对象粒度、migration、recovery、replay、scale、真实 Agent 分成独立研究单元。本批没有做 `overlay-fork`、`retention`、`packed-access`、`packed-lifecycle`、`object-granularity`、`migration`、`recovery`、`replay`、`scale`、`agent-validation`；也没有完成 qualification，因此不能为后续性能组开资格绿灯。

## 5. 一致性检查

| 检查 | 结论 | 证据 |
|---|---|---|
| `EXPERIMENT-CONFIG.zh.md` 与 `TEST-PLAN.zh.md` 的磁盘要求 | **FAIL（协议缺口）** | `EXPERIMENT-CONFIG.zh.md` 明确写 4×250 GiB PL1、系统盘不得承载测量 I/O、四 volume；`TEST-PLAN.zh.md` 写四台对象服务和资源，但没有把这组硬磁盘要求纳入其装置段。执行者只读 TEST-PLAN 会漏掉落盘检查。 |
| config 命名是否同一套 | **FAIL（字面不一致，语义可映射）** | `design.json`: `native/native-overlay/native-overlay-pack`；`run-grid.json`: `flat-v1`、`Overlay + native lower`、`Overlay + Packed lower`；TEST/JUICEFS 使用中文全称。应增加 canonical ID 到 display name 的唯一映射。 |
| template 字段与每格 `expected_artifact` | **FAIL** | `run-record.template.json` 没有 `cell_id`、`layer`、`judge`、`expected_artifact`；只有泛化的 `raw_artifacts`。执行记录无法机械验证是否产出了该格指定文件。 |
| 所有 `paper-design/...` 引用存在 | **FAIL** | 精确 grep 得到的唯一文件死链是 `paper-design/plan.zh.md`；`paper-design/tools` 是存在的目录而非文件。当前 TEST-PLAN 自己也承认 plan 缺失。 |
| 孤儿文件 | **发现 2 个，不删除** | `paper-design/REORG-EXPERIMENT-PLAN.zh.md`、`paper-design/JUICEFS-PARITY.md` 没有被其他文档以文件名/链接引用；它们内容重要，应从 README/TEST-PLAN 建立入口。 |
| `experiments/` 与 `results/` 被报告引用的证据 | **PASS（抽查路径仍在）** | `results/opencode-full-fuse-pytest-10356-20261023/README.md`、`experiments/agent-fuse-observations/README.md`、`experiments/cloud-smoke/2026-10-25/report.md`、其引用的 `results/juicefs-setup.json`、`experiments/cloud-smoke/2026-10-25/results/amp/raw-20261025.tar.zst` 均存在；它们是历史/pilot 证据，不是本批有效结果。 |

## 6. “文档说做了但没有仓库产物”

当前设计文件把状态写为 planned，且 `execution_enabled=false`，所以 19 种 `expected_artifact`（`qualification*.json`、`attribute-cache.csv`、`reopen-cache.csv`、`write-cost.csv`、`overlay-fork*.csv`、`retention*.csv`、`packed-access.csv`、`packed-lifecycle.csv`、`object-*.csv`、`migration.csv`、`recovery.json`、`replay.csv`、`scale.csv`、`agent-validation.json`）没有在仓库中出现是计划状态的结果。真正缺的是：仓库没有任何本批 `backend-pin.json`、`batch-manifest.json`、`ceilings.json` 或四配置 W1–W4 产物；TEST-PLAN 引用的 `/home/hxy/us-artifacts` 也不在仓库。

外部暂存的 C4 证据（只读检查，不写回仓库）显示：RustFS revision 门 G0 通过；但 `--workspace 0` 被 `brewfs-975deac` 拒绝（要求 32 位 simple workspace ID），3 次重试相同失败，G1 未挂载，G2 盘上限和 G3 EC/动态标记均 `unmeasured`，W1–W4 全部 `unmeasured(G1挂载失败)`。外部 `/home/hxy/BACKEND-PIN.json` 的 `brewfs_client_sha` 仍为 `null`，且其 `resolved_at` 是 2026-10-27，不是本批开跑时的完整 pin；因此这批不能被当作已钉住的正式批次。

## 7. 按严重度排序的问题和可执行修法

### blocker

1. **本批没有合格的批次 pin。** 现象：仓库没有本批 `backend-pin.json`；外部 pin 的 `brewfs_client_sha=null`，且不是 10-28 开跑时重新 `git ls-remote` 的完整记录。影响：RustFS 来源、镜像、BrewFS 客户端无法机械绑定，违反版本合同。建议：每批开跑前写完整 `backend-pin.json`，检查 image label 等于 frozen SHA；缺字段直接阻断。
2. **配置资格失败且没有有效执行记录。** 现象：C4 的 `--workspace 0` 被客户端拒绝，G1 失败后 G2/G3/W1–W4 停止；仓库 `execution_enabled=false`。影响：50 格均不能写入论文结果。建议：为 975deac 生成真实 32 位 workspace ID 和初始化 catalog，修正配置卡后以新批次重跑资格门，不能回填旧批次。
3. **计划有两个互相独立的机器可读源。** 现象：`design.json` 11 类，`run-grid.json` 14 类，新增三类没有冻结设计参数。影响：执行器无法知道哪一套是唯一依据。建议：把属性/重复打开/写入三类正式加入 `design.json`，或从 run-grid 移除并保留为附录；由校验器拒绝未声明实验。
4. **记录模板无法绑定格子产物。** 现象：template 无 `cell_id/layer/judge/expected_artifact`。影响：产物存在也无法自动证明属于哪格、测量层是否正确。建议：模板增加这些必填字段并用 JSON Schema/脚本逐格校验。
5. **`paper-design/plan.zh.md` 死链造成入口不确定。** 现象：多份历史文档指向不存在的当前路径。影响：新执行者会读到缺失入口或误用历史副本。建议：明确恢复为当前方案并标版本，或把所有链接改到现行 `TEST-PLAN`/`REORG`，同时在校验器中检查链接。

### major

1. **四组 W1–W4 与 50 格不是同一矩阵。** W2 没有网格行；W1 只与 write-cost 近似；W4 缺 packed 行；把本批称作覆盖 50 格会夸大证据。建议：在批次 manifest 写 canonical cell 映射，严格 workload/params 不匹配时标 `unmapped`。
2. **migration/scale 的单机边界没有在 cell 里显式标注。** 现象：网格列出 4/16/64/128 workers，但单机只有 8 核/30 GiB；migration 需要源/目标 worker。影响：执行者可能在单机伪造多节点实验。建议：cell 增加 `topology_required`、`resource_contract` 和节点身份门。
3. **layer 归属有五个写入/对象问题。** `write-*` 把 device bytes 混在 backend，`object-client-g` 把客户端 g 写成 backend。影响：缓存/设备数字会被错误归因。建议：拆分 `disk`/`client` 主层或允许多层字段并要求每项指标归属。
4. **9 格 judge 不能自动决定比较结论。** `access-*`、`lifecycle-*`、`agent-*` 的 judge 只有联合判断/观察交叉点/score，没有预注册阈值。影响：可重复生成数据，但不能自动生成“通过/优于”结论。建议：冻结完成阈值、资源上限和 score 判定规则。
5. **装置硬约束没有从 TEST-PLAN 链接到检查脚本。** 影响：执行者可能漏跑 4 盘、裸盘 ceiling 和系统盘 I/O 检查。建议：TEST-PLAN 的共同装置直接链接 `EXPERIMENT-CONFIG.zh.md` 和 `check-disk-layout.sh`，把脚本退出码作为资格门。

### minor

1. **`REORG-EXPERIMENT-PLAN.zh.md` 与 `JUICEFS-PARITY.md` 是孤儿文件。** 建议补入口链接，不删除。
2. **`run-grid.csv` 的 1174 行容易被误读为 1174 个独立 cell。** 建议在 CSV 头部或 README 明示 `cell_id` 与展开行的关系。
3. **计划把 `/home/hxy/...` 绝对路径写入输入和夹具。** 影响跨主机复现。建议批次 manifest 同时保存内容 SHA256 和节点上的解析路径。
4. **现有历史结果的版本口径不同。** 根 README 的历史结果仍引用旧 RustFS/BrewFS/JuiceFS；应在报告标题中显式标“历史 pilot”，不能与本批混列。

## 8. 实际改动

- 修改 [`paper-design/tools/check-disk-layout.sh`](paper-design/tools/check-disk-layout.sh)：缺失 `d1..d4` 现在直接 `FAIL`；新增四个 RustFS volume 必须来自四个不同设备的检查。原因是原脚本只警告缺盘且允许四个目录共用一个文件系统，可能静默放行 EC/动态布局失真的装置。影响所有未来执行批次的装置资格门；不读取、不修改实验数据。
- 新增本报告的仓库版本 [`paper-design/REPO-AUDIT.md`](paper-design/REPO-AUDIT.md) 和人读版本 [`REPO-AUDIT.md`](REPO-AUDIT.md)。没有修改 `experiments/`、`results/`、输入数据或历史证据。
