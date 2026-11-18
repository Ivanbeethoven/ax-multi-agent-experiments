# 运行与查看进度

完整实验包含 56 项，每个条件运行一次。配置见 `configs/experiment-suite.json`，服务地址和运行编号见 `configs/runtime.json`。

下一轮集群复测使用合并修复后的同一版 BrewFS，只选择此前落后的项目。JuiceFS 顺序冷读已单独复测一次，见 [C1 顺序冷读](../results/cluster-direct/juicefs-sequential-read.md)。其余 JuiceFS 条件沿用已有结果。按项目编号保存各次测量，核对输入、并发、缓存状态与持久化方式。C1 分别报告 fio 吞吐和按应用总耗时计算的吞吐。

## 修复前后的单机比较

C1 在 A672，C2 在 A635，C3 在 A355，C4 在 A601。各组先在同一台机器上用相同输入、缓存预算和后端配置比较修复前后，并保存两次测量的原始结果。需要对象存储时使用本机 RustFS，一个进程使用一个数据目录。A601 的端口为 28180，其余五台为 29118，均只监听本机。C2 的分页测试使用本机 TiKV 和原测试对象目录。

单机 RustFS 的 Compose 文件见 [compose.single-disk.yml](../infra/rustfs/compose.single-disk.yml)。复制到节点专用目录，在同目录的私有 `.env` 设置 `RUSTFS_IMAGE`、`RUSTFS_ACCESS_KEY` 和 `RUSTFS_SECRET_KEY`，让容器用户 10001 能写入 `data/`。执行 `docker compose -f compose.single-disk.yml up -d`。

单机性能测试中，RustFS 进程及新建实验桶均使用 `none` 持久化模式。RustFS 确认对象写入时，操作系统可能仍持有待落盘数据。保留 fio 和回放负载原有的 `fsync` 操作。记录实际桶策略，修复前后及文件系统对照使用相同策略。

各组完成单机对照后，将通过测试的修复合入同一版 BrewFS。用该版本重跑正式集群实验。清空正式两套 RustFS 前，先保存原始结果和后续需要的父版本，确认配套的元数据和对象能一并恢复。正式测试中 Dynamic 开启使用 9000，关闭使用原版 RustFS 的 19000；计时前分别核实两个版本对对象写入的落盘策略。

## Overlay 的父版本

普通 fork 使用 Native Overlay。C1 的工作区、C2 的子卷和 C4 的回放工作区均从已登记的原生父版本 fork。各子卷共用原生父层，各自有独立的 workspace ID 和可写层。父数据在实验期间保持只读。执行器在计时前读取 Redis 中的父层记录，确认该层存在且没有登记为 Packed carrier，将结果写入本次运行目录的 `native-parent.json` 或 `native-parents.json`。Packed 只用于 C3 的只读小文件实验。

在协调机的运行目录中按实验读取以下登记文件。复制登记文件到新的运行目录时，文件指向的父数据和对应后端也必须可访问。

| 实验 | 登记文件 |
| --- | --- |
| fork 与 Overlay | `prepared/fork/<tag>/parent.json`，包含系统、文件数量、文件大小、对象桶、父工作区和 BrewFS revision；JuiceFS Overlay 使用已有父分支 |
| Native 小文件多挂载读取 | `prepared/brewfs-native-tikv-<on或off>-<文件数>.json`，包含已有 revision。独立的 Native 上传计时写入新桶，读者从登记的父版本分别 fork |
| 多 Agent 回放 | `prepared/trace-bases-dynamic.json` 或 `prepared/trace-bases-official.json`，小规模检查使用同名的 `-check.json` 文件。每个 BrewFS 用例含 `base_ws`、`revision`、`namespace` 和 `bucket` |
| fio | BrewFS 在初始化卷返回的空父版本上 fork，再向子工作区写入输入。使用 JuiceFS Overlay 时从 `mounts/io-parents-<policy>/io-input-juicefs-overlay.json` 读取已有父分支 |

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

C1 的 `01-io-04-r1` 单项使用 `bench/cluster/runtime/retest_c1_randwrite.py`。在 A366 传入 `--binary`（统一版 BrewFS）、`--verifier`（用 `tools/fs-probe/src/bin/c1-verify-randwrite.rs` 编译的 Linux 程序）、`--reference`（原负载的八份 `writes-N.iolog`）、`--pattern`（原 fio 的 4 KiB 写入样本）和 `--output`（以 `attempt-01` 命名的新目录）。A366 写入，A355 冷重挂并逐页比较完整 2 GiB。结果记录 16,384 个已写页和 507,904 个未改页的校验，以及与历史偏移顺序的比较。原 04 项提供输入和缓存配置，候选版使用 50 ms 写入合并窗口。JuiceFS 采用 08 项的已有结果。

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
