# Agent 负载录制、回放与会话迁移

核查日期：2026-10-23。AX 集成与多 Agent 运行已具备；本文细化负载和会话的实验边界。后续已执行 [Windows → WSL 小任务测试](validation/opencode-resume-20261023/README.md)：直接复制数据库失败，原生导出/导入并告知目标路径后续跑成功。目标 HOME 为空，测试未创建云资源；下文完整集群测试仍是计划。

## 1. 推荐安排

**少量真实 Agent 录制 → 固定操作零模型 token 回放 → 少量真实会话迁移验证。** 大规模、重复次数和消融主要放在回放层；真实 Agent 用于提供负载来源，以及验证移机后确实继续任务。

- 录制：先用既定任务集中的两题检查采集完整性，通过后按原清单完成 16 题；两题计入 16 次，不额外重复。保留失败轨迹，不按存储收益或任务成功挑选。保存输入树、工具版本、模型配置、完整工具输入、实际工具输出和文件变化。
- 对比：每种存储和每个消融使用同一份输入与操作序列。模型等待采用同一份录制间隔；另测去掉模型等待的模式，两者分开报告。回放不调用模型 API，但仍消耗计算和存储资源。
- 验真：以 [回放实验计划](replay-experiments.zh.md#4-当前建议的真实模型试跑预算) 为唯一预算依据：建议 16 次采集加 32 次真实任务验证，共 48 次完整运行；另有两题 × B0/B2 × 迁移/不迁移，共 8 次续跑尝试。一批仅为试跑，B0 接入暂缓；不是已完成结果，也不能据此宣称稳定尾部统计。
- 若某种设计的优势只在回放出现、在真实运行中被模型等待盖住，应如实报告。固定轨迹的输出校验不能代替真实 Agent 的任务成功率。

## 2. 四层负载各回答什么

| 层次 | 固定内容与实际执行 | 模型 token | 能支持的结论与限制 |
|---|---|---|---|
| 真实 Agent | 固定任务、镜像、预算；模型实际决策，工具实际执行 | 有 | 端到端完成时间、正确率、原生会话续跑；操作序列可能不同 |
| 固定模型响应 | 向原 Agent 注入录制的模型响应，仍执行真实工具并写原生会话状态 | 无新增模型 token | 更接近 Agent 框架的保存开销；需专门响应适配器。工具结果若偏离录制预期，应停止并报告，不能偷偷回填旧结果 |
| 固定工具命令 | 按记录执行搜索、读写、补丁、测试命令，固定输入字节、顺序和依赖 | 无 | 工具耗时、读写与元数据开销；不证明模型会作相同决策，也不自动包含原生会话保存开销 |
| POSIX 操作经挂载点回放 | 对各系统挂载点执行相同文件接口操作、字节和依赖 | 无 | 存储机制和容量消融；不是逐条发送原始 FUSE 协议请求，缓存与预读会改变底层请求 |

先做固定工具命令和 POSIX 回放；固定模型响应应在适配器验证通过后再纳入，不能把它写成已有能力。原生会话写入若只由工具层模拟，须单独标记为合成状态负载。

旧 FUSE trace 缺少完整写入字节等信息，不能直接称为完整语义回放。字段、原始日志和补采要求以 [现有 trace 审计](replay-trace-audit.md) 为准，本文不重复其规则。

## 3. 可把会话放进 workspace 的 Agent

| Agent 与核查版本 | 本地记录与配置 | 恢复能力 | 本次确认边界 |
|---|---|---|---|
| OpenCode v1.19.4，2026-10-22 发布 | XDG data/config/state；SQLite 会话库；`export`/`import` | `run --session ID` | 稳定版源码确认；未做文件系统兼容及跨机器验证 |
| Claude Code 当前官方文档 | `CLAUDE_CONFIG_DIR`；JSONL 与附属文件；固定 project 名需 v2.1.234+ | `--resume ID` 或绝对 JSONL 路径 | 原生接口有文档；未在当前集群验证 |
| mini-swe-agent commit `04d809ceab9df28f9adaed044884180159172930` | `mini -o trajectory.json`，每步保存 | 当前 CLI 未发现原生恢复入口 | `run()` 会清空消息，不能将重新运行当恢复 |
| Codex CLI 0.164.0（本地帮助核查） | `--json` 输出 JSONL 事件；会话持久化另有存储 | `codex exec resume ID` | 不使用 `--ephemeral`；未验证通过搬迁 `CODEX_HOME` 跨机器恢复 |

主实验选用 **OpenCode**，先固定已核查的 v1.19.4 做兼容性试跑，锁定镜像后不在同一批实验中升级。选它是因为可以检查和修改源码、指定状态目录，并按明确的 session ID 续跑。Claude Code 留作第二个真实案例，不要求第一轮同时接通两套框架。这个选择是实验准备决定，尚不代表跨节点恢复已经通过。

### 先把一个 Agent 的恢复做简单

- 一个独立调度的 Agent 使用一个工作区、一套状态目录和一个独立 OpenCode 实例。不要让整台机器的 Agent 共用一个会话数据库，否则迁走一个 Agent 还要拆数据库。Agent 内部的子会话先随主 Agent 一起移动，不声称能独立迁走。
- 两端都将该工作区挂到 `/workspace`，项目路径固定为 `/workspace/repo`。会话、计划和必要工具结果放在 `/workspace/agent-state`；会话 ID、已完成步骤和累计预算放在 `/workspace/run-state`。共享的只读代码和数据可以由存储层复用，私有会话仍然分开。
- AX 包装程序准备三个操作：首次启动、停在安全位置并保存、按指定会话继续。后两项需要实现并验证；不能把向进程发送停止信号直接叫作安全保存。
- 正常迁移先停止发出新模型请求和工具操作，等在途工作结束，关闭源实例并保存整个工作区；释放旧写权限后，目标新进程使用相同版本和路径继续指定 session。启动命令与剩余预算由控制器恢复，凭据在目标注入。
- 首批只测文本仓库任务，不保留后台 shell、后台子任务或外部服务连接。恢复的是已保存任务状态，不是进程内存。复杂任务以后单独增加，不能把这个边界藏起来。
- 先做三步检查：同机退出后继续；另一台机器、空 HOME 继续；源机器离线后目标继续。每步都核对只存在于会话里的任务决定、已完成工具、文件内容和剩余预算，再实际完成后续工作。另测旧源返回后不能继续写。

SQLite/WAL 是主方案的前置检查项。每工作区单实例只减少共享访问，不能据此认定任意 FUSE/网络文件系统都支持 WAL。若直接保存数据库不兼容，单列“停止后导出、目标导入”的对照路线，计入全部导出导入时间并检查状态是否完整；不能悄悄把会话移到不计成本的本地盘。

### OpenCode 配置与限制

以下是待验证的启动配置，环境变量必须在 Agent 进程启动前设置；示例不包含凭据：

```bash
export XDG_DATA_HOME=/workspace/agent-state/opencode/data
export XDG_CONFIG_HOME=/workspace/agent-state/opencode/config
export XDG_STATE_HOME=/workspace/agent-state/opencode/state
cd /workspace/repo
opencode debug paths
opencode db path
opencode run --format json --session "$SESSION_ID" "继续原任务"
```

首次启动用 `opencode run --format json "任务提示"`，从结果记录 session ID。恢复时使用显式 ID，不用 `--continue` 猜测最近会话。缓存是否迁移另列配置；两端镜像、工具和依赖保持相同。

v1.19.4 默认数据库为 data 目录中的 `opencode.db`，使用 SQLite WAL 和 `synchronous=NORMAL`。先验证目标文件系统的锁、WAL、mmap、关闭与恢复行为；不能只复制打开中的 `.db`，也不能把应用写返回当作远端持久化完成。SQLite 官方要求 WAL 访问者在同一主机，不能据此假定任意共享挂载均兼容。

备用路径是停止执行后 `opencode export "$SESSION_ID" > session.json`，目标节点在正确仓库目录执行 `opencode import session.json`，再按原 ID 继续。将导出、导入计入停顿。源码的导出对象为当前 session 的 `info/messages`，不能当作包含全部子会话、undo snapshot 实体、外部附件与后台进程的完整备份。不要将脱敏导出用于等价恢复。

### Claude Code 配置与限制

```bash
export CLAUDE_CONFIG_DIR=/workspace/agent-state/claude
export CLAUDE_CODE_PROJECT_DIR_NAME=work
export CLAUDE_CODE_TMPDIR=/workspace/agent-state/claude-tmp
cd /workspace/repo
claude -p --resume "$SESSION_ID" --output-format stream-json --verbose "继续原任务"
```

首次运行可用 `--session-id` 指定合法 UUID，或从结构化结果取得 ID；`--session-id` 本身不等于恢复。`CLAUDE_CODE_PROJECT_DIR_NAME` 需同时设置 `CLAUDE_CONFIG_DIR`，使会话保存在 `projects/work/`，避免默认目录名随宿主路径变化。两端必须持续使用相同配置。

需保留 JSONL 的附属 tool-results、subagents、plans、memory 等状态；只搬一个 transcript 不能保证所有引用有效。新版图片附件可能在内部 temp 下，所以应收拢 temp，或明确首批只测文本任务。恢复会话不恢复运行中的工具、后台 Bash 或后台子 Agent；部分启动参数仍须重传。

`--replay-user-messages` 只是将输入用户消息回显确认，不是负载回放。不要启用 `--no-session-persistence` 或 `CLAUDE_CODE_SKIP_PROMPT_HISTORY`。`--max-budget-usd` 不把之前恢复出来的历史花费算入本次上限，因此总预算必须由实验控制器跨恢复累计。

### mini-swe-agent 与 Codex 的边界

mini 的 `-o` 可将轨迹写到 workspace；序列化包含 messages、模型调用数、花费和配置，但 `run()` 重置消息，新实例也重置计数。若实现恢复，需要加载这些状态、恢复时间预算与环境，并避开清空消息的入口。没有该扩展和验证前，只把它列为采集候选。

Codex 的 `--json` 是供脚本消费的事件流，并不单独构成可恢复会话包。应使用 `codex exec resume "$SESSION_ID" "继续原任务"`，避免 `--ephemeral`；CLI 可恢复的事实不等于已证明当前工作区包含全部持久状态。本轮不据此承诺 `CODEX_HOME` 搬迁可用。

## 4. workspace 应包含什么

```text
/workspace/
  repo/                       仓库、未提交修改、输入与任务产物
  agent-state/                会话、计划、记忆、工具结果、必要附属状态
  run-state/                  session ID、下一操作、累计预算与检查点清单
```

`agent-state/` 属于 workspace 的持久状态，不提交到 Git。真实续跑时保留必要内容，不把 API 凭据或整台机器 HOME 作为迁移依赖；凭据在目标节点注入。审计日志与测量日志放在外部结果目录，防止它们改变被测数据规模。不能把所有工具输出一律叫缓存然后丢弃：先区分可重建缓存和会话实际引用的文件。

检查点清单至少包含：workspace/version ID、Agent 版本、session ID、最后完成工具序号、文件与会话内容摘要、累计模型调用/花费/输出 token、剩余预算、镜像摘要、配置摘要和写者身份。会话与文件必须对应同一工具边界，不能分别取得不同时间的“最新版本”。

## 5. 原生会话迁移的资格测试

1. 源节点用固定镜像和 `/workspace/repo` 路径运行。首批限制为无外部副作用的仓库任务，在第 3 次工具完成后请求暂停；若此边界不能安全停下，记录不支持，不能仅按日志行数杀进程。
2. 阻止下一次工具和模型请求；等待在途工具、模型请求、后台子任务结束。会话写入与文件修改均保存，记录同一边界的版本、序号和预算。强制终止是另一类故障实验，不能算正常迁移。
3. 停止源 Agent，确认无旧写者；完成存储保存及写权限交接。目标只能在交接完成后写。把暂停、保存、排队、交接、重新挂载、恢复全部计入时间。
4. 目标使用空 HOME 和相同容器内路径，仅恢复 workspace 与所需配置，再注入凭据。禁止读取源节点 HOME，确保成功不是依赖隐藏共享目录。
5. 按显式 session ID 恢复。先核对会话、文件版本、最后工具与剩余预算，再实际运行下一项工具；校验其输入、输出和后续任务结果。同 ID、能查看日志或能复述历史都不足以判成功。
6. 设置负对照：只恢复 repo、不给 agent-state，优先用无模型的会话加载检查，或在适配器可用后用固定模型回答验收。若需要真实模型，另列调用预算，不计入上述 8 次续跑。测试任务应含仅在历史会话出现的必要决策，续跑提示不能重述该决策；避免 Agent 靠扫描代码重新推理就“通过”。
7. 设置正常对照：同任务、同边界、同预算不迁移。真实模型续跑可能产生不同后续操作，这种差异是实测结果，不能强制替换为录制输出。

正常迁移的主指标：从暂停到目标首次正确后续工具完成的时间、任务最终正确率、额外模型调用与工具调用、重复读写量、迁移字节、累计总花费。区分恢复准备时间与后续模型等待。失败、恢复到旧版本、预算重置和重复副作用分别报告，均不能从分母中删除。

## 6. AX 接入点与证据边界

用户已确认 AX/BrewFS 集成和多 Agent 运行可用。本文不把本地默认 runner 的职责边界当成当前部署尚未实现的结论。

本地 `ax-integration-upstream` HEAD 为 `4ef115f88f3e0eedd2c2d9653fe04a25c1e158c6`；核查文件可能包含工作区修改，应随正式实验另存文件摘要。默认 runner 从 task command 新建子进程并设置工作目录/环境，停止时向进程组发送信号。这不会自行给任意 Agent 补上会话恢复参数。应在现有 task command 或包装入口中明确“首次启动/加载已有 session”分支，并核查用户部署已有实现。

- [runner.go 第 140 行，读取 command](https://github.com/Ivanbeethoven/ax/blob/4ef115f88f3e0eedd2c2d9653fe04a25c1e158c6/runner/runner.go)
- [runner.go 第 147 行，启动新进程与工作目录](https://github.com/Ivanbeethoven/ax/blob/4ef115f88f3e0eedd2c2d9653fe04a25c1e158c6/runner/runner.go)
- [runner.go 第 189 行，停止子进程组](https://github.com/Ivanbeethoven/ax/blob/4ef115f88f3e0eedd2c2d9653fe04a25c1e158c6/runner/runner.go)
- [runner.md 第 50 行，退出前保存续跑所需状态](https://github.com/Ivanbeethoven/ax/blob/4ef115f88f3e0eedd2c2d9653fe04a25c1e158c6/docs/runner.md)

## 7. 官方依据

- OpenCode：[v1.19.4 release](https://github.com/anomalyco/opencode/releases/tag/v1.19.4)、[CLI 文档](https://opencode.ai/docs/cli/)、[存储目录](https://opencode.ai/docs/troubleshooting/)。网页受限时读取了同项目官方文档源码与下列稳定版源码。
- OpenCode 稳定版：[XDG 目录](https://github.com/anomalyco/opencode/blob/v1.19.4/packages/core/src/global.ts)、[数据库与 WAL 配置](https://github.com/anomalyco/opencode/blob/v1.19.4/packages/core/src/database/database.ts)、[export](https://github.com/anomalyco/opencode/blob/v1.19.4/packages/opencode/src/cli/cmd/export.ts)、[import](https://github.com/anomalyco/opencode/blob/v1.19.4/packages/opencode/src/cli/cmd/import.ts)、[run](https://github.com/anomalyco/opencode/blob/v1.19.4/packages/opencode/src/cli/cmd/run.ts)。
- SQLite：[WAL 的文件、检查点与同机访问约束](https://www.sqlite.org/wal.html)。这说明需做兼容性验证，不构成已测得任何被比文件系统不兼容的结论。
- Claude Code：[会话恢复与 project 目录命名](https://code.claude.com/docs/en/sessions.md)、[环境变量](https://code.claude.com/docs/en/env-vars.md)、[CLI 参数](https://code.claude.com/docs/en/cli-reference.md)、[数据目录与附属文件](https://code.claude.com/docs/en/claude-directory.md)。本轮读取官方 `.md` 正文。
- mini-swe-agent：[CLI](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/run/mini.py)、[默认 Agent 的 run/save/serialize](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py)。
- Codex：[CLI reference](https://developers.openai.com/codex/cli/reference/)、[非交互执行](https://developers.openai.com/codex/noninteractive/)、[CLI features](https://developers.openai.com/codex/cli/features/)。主任务已实际读取官方页面并检查本地 0.164.0 的 `exec resume --help`。

正式实验前锁定实际运行版本、镜像摘要和配置；文档支持的功能、本地实现审查、资格测试通过与性能收益是四种不同证据。
