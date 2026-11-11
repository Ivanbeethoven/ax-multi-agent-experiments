# A C4：从 Off1 之后续跑

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

容量文件使用 `schema: c4-resume-stage-capacity-v1`、`passed: true`、`reviewed: true`。填入 `original_request`、原批次 `capacity.json` pin（`legacy_capacity`）、原 `runtime` pin、`pool`、`cell_id`、`step`、`prefix`（Off1 原 `finished.json` pin 加续跑请求的 `prefix`）、`archive_mode`（`original` 或 `closed-observer-direct`）、`reserve_fraction: 0.2`、`at`（带时区）。`snapshot` pin 必须来自原容量工具在上一轮完成后的新观测；`placement` pin 必须来自实际目录设备；`source_pins` 同时保留这两份观测和峰值估算依据。`coordinator` 填本轮 `out` 的父目录 `path` 及其 `stat` 返回的整数 `device`。

`placement` 使用 `schema: c4-archive-device-placement-v1`、`at`、`runtime`、`pool`、`workers`。每个角色填 `path`（容量观测中的盘路径）、`boot_id` 和 `targets`（该角色实际根目录下 `archives`、`evidence` 以及观察开启时的 `o`，路径 → 原生 `st_dev`）。On 和正式轮含三角色，Off 仅 client。某路径还不存在时，只读核对最近存在的祖先设备；若这三个目录不在同一数据盘，先核对实际分盘预算，本入口不直接放行。

`rows` 覆盖所有原生数据盘、Redis 内存和 AOF，以及协调者盘。每行填 `role`、`path`（Redis 行为 null）、`quantity`（`bytes`、`inodes`、`redis-memory-bytes` 或 `redis-aof-bytes`）、三个非负整数 `data_growth`、`observer_raw`、`archive_peak`。保留本轮原始采样、归档 tar、非观察者 staging 和协调者解包原文的峰值；原归档还计入第二份 observer staging。AOF 行标记 `included_in_metadata_disk_forecast: true`，元数据盘留原重写空间。写明峰值依据，不能用旧七份副本预算替代这份逐轮容量预算。

某轮失败，保留原证据，工具不自动重跑。原批次目录和原试验输出路径不会被改名或删除。