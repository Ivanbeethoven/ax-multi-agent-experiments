# A C4：Off1 后续跑的原手册（已撤出）

> BrewFS Flat 完整拷贝臂已撤出。下列请求与命令用于理解旧回执，不用于提交下一轮。[历史归档](OSS-ARCHIVE.zh.md)照原记录保留。

原批次仍是 Off1、On1、On2、Off2、Off3、On3、正式轮。Off1 结束后，原调度器必须自己回收子进程并写下 `trial-1/exit.json`；原来的空 `trial-2/` 停点保留。工具核对原服务的终态、三角色收尾、原锁、所有阶段的租约和 Off1 的完整冷校验。没有这些证据，不能接 On1。

每次提交一份审核后的请求，只运行一轮。指定 `step: 2` 为 On1；依次到 `step: 6`（On3），`step: 7` 才运行原校准验收和正式轮。用原代码目录保持原 `calibrate-c4.py`、`run-c4.py`、`check-c4-calibration.py` 和 `admit-c4.py` 的 SHA；新脚本放在另一份完整工具目录。先加 `--validate-only` 核对原终态、锁、容量；执行时去掉该选项。两次都要重新通过门槛。

```bash
python3 resume-c4-after-directory-hold.py --request "$request" --sha256 "$sha256" --validate-only
python3 resume-c4-after-directory-hold.py --request "$request" --sha256 "$sha256"
```

请求 JSON：

| 字段 | 内容 |
| --- | --- |
| `schema`, `reviewed` | `c4-after-directory-hold-v1`, `true` |
| `original_request`, `original_batch_tool`, `continuation_tool` | 分别 pin 原批次请求、原 code19 批次工具和 `continue-c4-formal.py` |
| `original_unit`, `original_invocation` | 原父批次的 systemd 服务和 invocation ID |
| `step`, `prefix`, `out` | 轮号 2–7；之前每轮本工具生成的 `result.json` pins（On1 时为空）；本轮新的协调者证据目录，父目录已存在 |
| `capacity` | 本轮审核过的容量文件 pin |
| `archive_overlay` | 可省略。On1/On2/On3：`runner` pin `calibrate-c4-closed-observer.py`。正式轮：`runner` pin `run-c4-formal-closed-observer.py`，另加 `calibration_overlay` pin `calibrate-c4-closed-observer.py`。两者均需 `helper` pin `archive-c4-closed-observer.py`，以及 `worker_code`：client、metadata、objects 各自的原 `CODE_FILES` 相对路径 → SHA-256。 |

所有 pin 使用 `{"path":"/绝对路径","sha256":"..."}`。打开归档入口后，只有归档阶段的已关闭 `o/<tag>` 直接打包。原闭合校验、其他目录的快照、归档完整性校验、统计和正式验收继续走原代码。正式轮的八个归档前阶段继续用原 `run-c4.py --stage` 命令和输入。若启用新归档，正式轮仍保留完整冷校验和全部观察原文，容量只扣除 worker 第二份 observer staging；省略入口时仍须预留这份 staging。

每轮先做清理检查，再测容量。两步均在原阶段启动前完成，不计入测量时间。C4 入口只核对清理回执，不删除 workspace、对象或证据，也不触发 GC。其它 C1–C3 轮继续使用各自的闭合与容量步骤。

容量文件使用 `schema: c4-resume-stage-capacity-v1`、`passed: true`、`reviewed: true`。填 `original_request`、`legacy_capacity`（原 `capacity.json` pin）、`runtime`、`pool`、`cell_id`、`step`、`prefix`（Off1 原 `finished.json` pin 加续跑请求的 `prefix`）、`archive_mode`（`original` 或 `closed-observer-direct`）、`reserve_fraction: 0.2` 和带时区的 `at`。`source_pins` 必须包含以下不同的原始文件：

- `cleanup`：`pilot-prestep-cleanup-v1` 回执，带 `pool`、`original_request`、`step`、`prefix`、`finished_at`、`outcome`、`actions`。确实清理过才填 `cleaned`，并列出通过且已执行的原 reclaimer 结果 pins；无需清理填 `not-needed` 和空数组。每个 worker 的 `snapshot` 原生采样及 `placement` 均须在本轮检查之后。
- 原 `legacy_capacity` 的 `before` 和 `after` pins：重算原盘数据增长，不用新的总用量替代。
- `observer_basis`：每角色一组已正常停止的原 `raw/<role>/o/<tag>/finished.json` pins，从对应 `sample.json` 和实际文件大小重算每秒速率和单次峰值。`observer_seconds` 是审核过的整轮观察时长，含启动、冷验和收尾；每个 On 轮和正式轮都须在 `source_pins` 中加入原 A Off1 的 `finished.json`、其绑定的 `cohort/result.json` 和完整冷验 `verify/result.json`，以及同一轮的 `drain-timing.json`。最短时长取 Off1 实测执行、执行结束确认到排空启动的间隔、排空和完整冷验之和，向上取整；历史短采样只能用于估算速率，不能缩短这段时长。该值不含挂载、卸载或观察者启停的完整开销，审核时还要给这些阶段和本轮波动留余量。Off 填空映射、时长 0。
- `archive_basis`：每个会归档的角色各填一个已核验的原 `archives/<role>-check.json` pin。工具从实际 tar 的 SHA、清单和长度核算非观察目录暂存及 tar 占用，按预计原文规模放大历史压缩占用的最低估算。On 和正式轮都要三角色，Off 只要 client。旧归档另加完整的 observer 副本，新归档只省该副本。
- `snapshot` 和 `placement`：当前原生盘及实际目录设备的 pins。`placement` 用 `c4-archive-device-placement-v1`，含 `at`、`runtime`、`pool`、`workers`；每个归档角色填容量快照中的盘 `path`、`boot_id` 和实际根目录下 `archives`、`evidence`、以及 On/正式轮的 `o` 路径到原生 `st_dev` 的映射。

`coordinator` 填本轮续跑 `out` 的父目录 `path`、整数 `device`，以及 `targets`（续跑 `out`、原 trial/formal 输出、其 `archives` 和 `raw` 路径到整数设备号的映射）。工具按实际路径或最近已存在祖先核对设备并读取可用空间；只允许这些路径同盘后合并预算。分盘时先重新核算，不可拿续跑目录的空闲量代替实验盘。

`rows` 覆盖所有原生数据盘、Redis 内存和 AOF，以及协调者盘。每行填 `role`、`path`（Redis 行为 null）、`quantity`（`bytes`、`inodes`、`redis-memory-bytes` 或 `redis-aof-bytes`）和三个非负整数 `data_growth`、`observer_raw`、`archive_peak`。AOF 行标记 `included_in_metadata_disk_forecast: true`，元数据盘留原重写空间。计入全部原始采样、协调者解包、tar 和暂存峰值后，实际盘仍须至少留 20% 字节和 inode。历史数值仅给出不得低于的预算，审核仍需为本轮预计峰值留足空间。

某轮失败，保留原证据，工具不自动重跑。原批次目录和原试验输出路径不会被改名或删除。
