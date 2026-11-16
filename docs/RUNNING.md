# 运行与查看进度

每个条件运行一次，共 56 项。配置为 `configs/experiment-suite.json`；服务地址和运行编号为 `configs/runtime.json`。

## 准备与启动

在本机仓库根目录执行：

```powershell
python infra/idcai/sync_suite.py
ssh.exe idcai-60001 /srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/direct/scripts/setup_suite.py
python infra/idcai/start_suite.py
```

`sync_suite.py` 分发运行代码；`setup_suite.py` 启动两套 RustFS，配置专用 Redis/TiKV 和六台采集器。准备只在开始一轮实验时执行。`start_suite.py` 启动后台 systemd 服务，按四组顺序执行，跳过已通过条件；出现错误时保存现场并退出。修复后使用同一启动命令继续。

只执行一组时使用 `python infra/idcai/start_suite.py --group 01-io`。其它组为 `02-fork`、`03-packed`、`04-agent-replay`。

已运行的计划需要补充原版独立卷时，等待当前服务结束，再执行 `python infra/idcai/start_suite.py --extend-plan --group 01-io`。该操作保留已有条件和结果，加入 `01-io-17-r1` 至 `01-io-20-r1`，随后只执行尚未通过的 fio 条件。

使用 `--start-at <条件编号>` 可从指定条件顺序执行后续实验，已完成条件自动跳过。此前条件的状态和数据保持原样；读取实验需要对应准备条件已经通过。

Packed 准备已生成 `volume.json` 时，可复用已上传的数据并重新执行跨机内容检查，通过后自动继续剩余条件：

```powershell
python infra/idcai/start_suite.py --reuse-prepared /srv/ax-cluster/runs/direct/run-002/03-packed/02/r1/attempt-01/volume.json
```

执行器核对原任务参数，保留原准备时间和文件位置，在新的 `attempt-NN` 中记录检查结果。读者测试只使用检查通过的数据。

小输入功能检查复用同一个执行器：

```powershell
ssh.exe idcai-60001 /srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/direct/scripts/run_suite.py --check --job 01-io-01-r1
```

正式实验开始前，可一次检查 43 个代表性条件，覆盖四组的执行路径：

```powershell
ssh.exe idcai-60001 /srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/direct/scripts/check_suite.py
```

使用 `--group 01-io` 只检查 fio；使用 `--list` 列出条件。已通过的检查自动跳过，首次失败时退出。正式后台服务运行期间不执行功能检查。

检查结果保存到 `checks/`，正式结果保存到对应组目录。首次启动前应完成 BrewFS、JuiceFS、TiKV 和 Packed 的实际挂载与读写检查。

## 单次检查

```powershell
ssh.exe idcai-60001 python3 /srv/ax-cluster/direct/scripts/status_suite.py
ssh.exe idcai-60001 systemctl status ax-run-002 --no-pager
ssh.exe idcai-60001 tail -n 30 /srv/ax-cluster/runs/direct/run-002/logs/suite.log
```

输出按组显示完成数：fio 20、fork 12、Packed 11、多 Agent 13。收到进度提醒后执行一次检查；服务正常运行时让后台继续。出现错误时查看对应 `failure.json`、命令输出和挂载日志，修复并重测该项。

## 指定条件补测

协调服务停止后，使用 `reset_suite_groups.py --job <条件编号> --label <归档名称>` 重新安排指定条件；多个条件重复传入 `--job`。原始 attempt 目录保留，上一版汇总和状态移入 `archive/results/<归档名称>/`。随后用 `run_suite.py --job <条件编号>` 执行。

需要后台等待时，使用 `repeat_suite_jobs.py --job <条件编号> --label <归档名称> --execute`。它等待当前计划全部通过、协调服务和 `--wait-service` 指定服务结束，并确认可用磁盘空间至少为 16 GiB 且不低于总容量的 12%，再占用同一个协调服务名称顺序补测。`--require-job` 可指定尚待追加的条件。状态写入 `continuations/<归档名称>.json`。不传 `--execute` 时只检查等待条件。

对于使用独立输入和后端的补测，可为无数据依赖的条件逐项指定 `--wait-terminal-job <编号>`。这些条件必须结束，原有通过或失败记录保持不变。正在运行、尚未执行或缺少状态的条件仍需等待。集群独占和磁盘要求相同。

## 目录与结果

- 协调者：A366，`idcai-60001`。
- 当前代码：`/srv/ax-cluster/direct/repo`；执行脚本：`/srv/ax-cluster/direct/scripts`。
- 二进制：`/srv/ax-cluster/direct/bin`；输入：`/srv/ax-cluster/input`。
- 当前结果：`/srv/ax-cluster/runs/direct/run-002`。
- 各条件：`<组>/<配置编号>/r1/attempt-NN`，保存配置、命令输出、指标、检查结果。
- 六台监控：各自的 `hosts/<主机>/`，每 5 秒采样，阶段边界额外采样。
- 旧结果：服务器 `run-001`；本地历史材料位于 `archive/`。

实验完成后在本机一次性取回：

```powershell
python infra/idcai/collect_direct_results.py --out results/run-002
```

该脚本先把六台监控文件汇总到 A366，再下载结果，并核对文件数量和长度。缓存、活动挂载、公共输入和凭据不进入结果包。

## 数据保留

**在回放之后补测 Packed。** 在 A366 用下列命令排队。指定与四挂载点验证相同源码的 release 优化版本；二进制旁的 `build.json` 记录构建参数、长度及验证目录。脚本等所有回放通过、正式运行服务与先前安排的 fio 准备任务结束后，占用同一个正式运行服务，分发二进制并依次测量三个条件。状态位于 `continuations/packed-read-formal.json`。

```bash
/srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/direct/scripts/queue_packed_reads.py \
  --expected-run run-002 --binary /srv/ax-cluster/releases/packed-auth-fields-v4-opt/brewfs \
  --job 03-packed-07-r1 --job 03-packed-10-r1 --job 03-packed-11-r1 \
  --wait-service ax-flat-fio-priority.service
```

**在 RustFS 节点运行多 Agent 回放。** 将 `configs/replay-placement-rustfs.json` 复制为本轮结果目录的 `replay-placement.json`。16、64 Agent 的三个系统及 Dynamic 关闭条件均使用相同分配：A672/A601/A635/A368 分别承担 2/6/6/2 或 8/24/24/8 个 Agent。冷重挂在另一台机器上完成。A366 继续协调和汇总，A355 提供元数据。1、8 Agent 沿用原分配。原始结果的 `tasks-plan.json` 记录每项任务的测量和校验主机，分析时注明客户端与对象服务共用主机。已经启动的旧版运行程序需在完整条件结束后重新启动才能读取该配置。

运行期间保留各项测试的子工作区和对象数据，挂载退出后清理客户端本地缓存。整轮结果取回后，再统一重建本轮两套 RustFS 的专用数据目录。下一轮使用新的运行编号和元数据命名空间，重新准备输入。

已有 `measurement.json` 且读回校验、卸载均完成时，可在协调进程停止后使用 `python3 /srv/ax-cluster/direct/scripts/finalize_fio_checkpoint.py --job <job-id>` 生成结果，再用原启动命令继续。
