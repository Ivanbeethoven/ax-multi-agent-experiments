# 开跑前和运行后检查什么

这些检查防止拿错输入、少跑任务、混用文件或把没有生效的开关当作消融。通过检查仍不能直接进入论文性能图：分布式运行返回的 `measurement_valid` 始终为 `false`，还要检查真实运行记录。当前没有合格的 16 份真实工具轨迹。测试代码里的小文件全部是合成输入，只验证检查程序。

## 1. 工具回放先准备哪些文件

`rq3-replay` 和 A6 的 cell 必须填：

| 字段 | 要填什么 |
|---|---|
| `parameters.trace_manifest` | 回放清单文件；绝对路径，或相对本实验目录的路径 |
| `parameters.trace_manifest_sha256` | 清单文件原始字节的 SHA256，64 位小写十六进制 |
| `parameters.workload_kind` | `tool_replay` |
| `parameters.live_model_calls_allowed` | `false` |
| `parameters.divergence_policy` | `stop_task_no_model_fallback` |
| `parameters.tasks` | `16` |
| `parameters.state_execution` | `excluded`、`recorded_state_io` 或 `native_runtime`；省略时按 `excluded` 检查 |

回放清单使用 `schema_version: 1`，包含 `workload_kind: tool_replay`、`state_execution`、`frozen_tasks_sha256` 和 `tasks`。其中 `frozen_tasks_sha256` 必须等于现有 `inputs/live-tasks.json` 的实际摘要。`tasks` 必须恰好覆盖其中的 16 题，不能重复、删掉失败题或替换题。

每题填写以下字段：

```text
instance_id、base_commit、prompt_sha256：与冻结任务一致
image_digest：sha256:<64位摘要>，也可带 image-name@ 前缀；不能填 latest
state_execution：与整个清单一致
trace：{path: "trace/该题.jsonl", sha256: "实际文件摘要"}
qualification_events：{path: "check/该题.jsonl", sha256: "实际文件摘要"}
```

`path` 相对于回放清单所在目录，只能引用这个目录以内的文件。程序会打开文件重新计算摘要，不接受只写 `qualified: true`。这里只比较保存的输入和逐步结果，不能靠这些 JSON 证明远程机器确实执行过；后续还需检查运行记录。

工具轨迹的第一行字段如下，后续每行是一条工具操作：

```text
第一行：schema_version=1，format=ax-tool-trace，workload_kind=tool_replay，
        instance_id、base_commit、image_digest、state_execution，
        live_model_calls_allowed=false，divergence_policy=stop_task_no_model_fallback
操作行：step_id="唯一编号"，op=tool，argv=["程序", "参数"...]，
        cwd="/workspace/内的固定目录"，depends_on=["已完成的前序编号"...]，
        expected={exit_code: 0, stdout_sha256: "...", stderr_sha256: "...", files_sha256: "..."}
```

`qualification_events` 是一次回放检查留下的逐步结果：第一行使用 `format=ax-tool-replay-qualification`，写 `schema_version=1`、同样的任务/版本/镜像/状态字段、`trace_sha256`、`model_calls=0`、`model_fallback_used=false`。后续每步写 `step_id`、`status=matched`，以及实际 `exit_code/stdout_sha256/stderr_sha256/files_sha256`。程序检查步骤数量、次序和每步结果是否匹配；缺步、偏离或调用模型均拒绝。

这是一份供记录器和工具回放器对接的最小格式；现有 Rust 文件回放器的 `mounted-fs-replay` 格式不能直接替代它。`state_execution` 只说明输入打算怎样处理会话状态，不能凭这个字段宣称恢复了真实 Agent。

## 2. 数据和实验记录放在哪里

每个 cell 的数据放在 `workspace_root/cell-<cell_id摘要>/distributed`。运行记录放在这次输出目录的 `distributed-artifacts`，通过 `artifact_output_dir` 交给适配器。两个目录都必须不存在，重复运行不会覆盖旧文件。

适配器不能从自己的配置或请求内容增加记录目录权限；允许写入的根目录由 `run_cell.py` 创建本次运行时传入。仪器记录不能放进被测数据目录，也不能放进适配器的 stdout/stderr 日志目录。真正的 Agent 会话文件仍属于工作区数据，不能借这个安排移出去。

`cell.json` 按输入的原始字节保存，因此 `result.cell_sha256` 可以重新计算核对。即使适配器初始化失败，合法 cell 的编号、基线和摘要也保留。

## 3. 冷、暖缓存怎么记录

正式 cell 使用两个协议之一：

- `client-cold-backend-observed-v1`：客户端冷缓存。
- `client-warm-backend-observed-v1`：客户端暖缓存。

`measurement_evidence.cache_observations` 必须是对象，包含 `protocol_id`、`client_state`、`reset_after_validation`、`backend_state`。前两项必须与 cell 一致；`reset_after_validation` 是布尔值。后端状态可填 `cold/warm/unknown/managed_uncontrolled/mixed`，不知道就明确写不知道，不将客户端冷缓存当作云服务冷缓存。

冷客户端还必须有 `reset_after_validation=true`，以及 `actions.page/dentry/process/filesystem` 四条处理记录。每条写 `method` 和 `receipt: {path, sha256}`；记录文件在本次 `distributed-artifacts` 内，内容为：

```text
cache：page、dentry、process 或 filesystem
method：与 actions 中相同的处理方法
completed：true
after_validation：true
```

程序核对文件摘要和这些字段。操作是否真的清除了对应缓存，仍需核查实际命令、进程和文件系统记录；这些字段本身不授予性能结果资格。暖客户端不强制执行冷重置，也不能写成冷客户端。小规模本地脚本检查不必执行这套冷缓存流程，但不产出正式性能结果。

## 4. 消融必须报出实际使用的选项

`mechanism_receipt.variant` 必须等于 cell 的变体名，其他字段按下表检查：

| 消融 | 必须匹配的值 |
|---|---|
| A1 | `shared_base`、`packed_metadata` 为布尔值，与 shared/private、packed/plain 分别匹配 |
| A2 | `save_mode` 为 `private_delta` 或 `full_materialization`；`remote_ack=true` |
| A3 | `metadata_representation` 为 `packed/equivalent_plain`；`cache_mode` 与本批 cold/warm 一致 |
| A4 | `layout_policy=fixed/selected`，`range_mode=range/whole_get`；对象摘要为 SHA256；`B/q/ec_data` 为正整数，`ec_parity` 为非负整数 |
| A5 | `transfer_mode=reopen_remote_state/copy_then_resume`；`ownership_protocol` 写协议 ID、`single_writer=true` 和 `stale_publication_rejected=true` |
| A6 | 完整组为 packed/private_delta/selected；每个删减组只改对应的 metadata/save/layout 字段 |

所有正式运行的 `elapsed_s` 必须有限且非负，`deadline_s` 必须有限且大于零。数量必须符合计划，SHA256 字段必须是 64 位小写十六进制。检查失败返回 `rejected` 并保留原因；检查通过也仍是“材料齐全且相互一致”，不是性能结果已经可信。

## 5. 暂停后保存和故障测试需要哪些操作

适配器只说支持 `run_scenario` 不够。新场景先检查 `discover` 返回的 `data.scenario_capabilities`，其 `schema_version` 必须为 `1`。没有这些字段就拒绝开跑。

暂停前还有新修改的迁移测试，要求 `dirty_checkpoint` 对象中 `supported`、`write_without_checkpoint`、`save_after_pause` 都为 `true`。`hooks` 必须包含 `write_without_checkpoint`、`pause_selected`、`save_after_pause`、`confirm_checkpoint`、`resume_selected`，以及该 cell 额外列出的所有 `required_hooks`。这样可以区分“写完后尚未保存，暂停后再保存”和普通迁移。

故障测试要求 `faults.<fault_case>` 对象中 `supported=true`，`hooks` 覆盖该 cell 的全部 `required_hooks`。例如只支持 `after_ack` 的适配器不能拿去跑 `before_ack`；只有停止进程的操作，也不能自动代替旧写者返回或发布中断测试。

这些声明只检查适配器是否配置了所需操作。实际故障是否发生、恢复了哪一版、旧写者是否还能发布，仍须检查这次运行留下的记录；不会因此得到性能结果资格。A4 的 `scattered` 只改变读请求，仍按同一套布局和 Range 字段检查。

## 6. 怎样汇总结果

在本实验目录执行，使用生成计划时得到的 `index.json`，把收到的结果文件逐个列出：

```text
python -m harness.analyze --campaign runs/replay-plan/index.json --results runs/run-001/result.json runs/run-002/result.json --output runs/summary-001
```

`--output` 必须是新目录。若一批都没有运行，可以只给 `--campaign` 和 `--output`；汇总仍列出计划中的所有 cell。缺结果的 cell 标为 `not_run`，不能从计划总数中消失，也不能当作已经失败或已经成功的任务。输出为 `summary.json` 和 `cells.csv`。

程序检查计划清单中的 cell 摘要，核对每份结果的 cell 编号、基线和原始字节摘要；改过输入、串用了其他批次或数量与计划不符时拒绝汇总。同一 cell 的多个重试不能直接混在一起。

如需计算配对时间差，命令另加 `--reference B2:default --treatment B0:default`。没有完成下面检查的记录仍可以列出状态，但不参加加速计算。

检查者看过原始结果后，在对应 `result.json` 同目录另存 `performance-audit.json`。它包含 `schema_version=1`、`status=accepted`、`reviewed_by`、`cell_sha256` 和 `result_sha256`。单独保存这个文件，是为了避免将审核结果写进被计算摘要的 `result.json` 后形成循环。

`artifacts` 必须引用七份材料：`items`、`events`、`inputs`、`resources`、`cache`、`durability`、`correctness`。每项写 `{path, sha256}`；路径相对于本次结果目录，只能引用该目录内的文件。脚本重新计算所有摘要，并从 `items` 检查任务数量、编号、状态和成功数。已检查的结果或材料后来发生改变，这份审核就不再有效。

`checks` 的字段与检查内容如下，检查者确认后才填写 `true`：

| 字段 | 要看什么 |
|---|---|
| `inputs_and_resources` | 任务、文件、镜像和分配资源是否符合计划 |
| `clocks_and_cache` | 计时起止和缓存状态是否符合本批规定 |
| `durability` | 保存完成是否达到规定的远端确认条件 |
| `correctness_and_coverage` | 正确性检查是否通过，失败、超时和未开始任务是否都保留 |
| `mechanism_and_operation_mode` | 实际运行的方式和消融选项是否与计划一致 |

这些文件检查不能代替人查看原始结果，也不会自动把 `performance_claims_allowed=false` 改成 `true`。当前分布式适配入口仍不自动授予性能资格；故障检查和本地小测试也不会因为材料齐全而成为性能实验。
