# A 组 C4：BrewFS Flat 完整拷贝

回答一个具体问题：一个 Agent 要从同一份已持久化的工作区开始独立工作时，BrewFS Flat 完整拷贝要等多久、读写多少数据？它是工作区共享派生的对照。`cp -a --reflink=never` 与 B 组 JuiceFS `clone -p` 比较的是两种实际创建方式；两者的差值不能单独归因到 BrewFS 或某个设计。若要比较文件系统在完整拷贝上的成本，还须单独登记 JuiceFS 同命令的对照。

- **输入**：A 组已准备 `/srv/ax-client/inputs/pilot-100k-01/fork-source`。100,000 个 4 KiB 文件，共 409,600,000 B。树 SHA256 为 `02aeda272edbab9512595f7b968d840a9cfdc0e8302ed036399bf4151f08bd13`，与 B 组一致；不是 C3 的 `smallfiles-source`。A 组原收尾、实际文件数及关闭回执见 [输入准备记录](evidence/preparation/pool-a-next-01/)。不重生成、不从本机传数据。
- **操作与输出**：冻结原 C4 单元 `c5e97774bcffa69a-r1-native`。一次导入、fsync、原生零上传债务及全量冷读通过后，在同一个持久化父工作区分别创建 6 个校准子目录和 1 个正式子目录；每次新建路径。从启动 copy 到第一次成功核对读出的 4 KiB 为主指标，分报 copy、首读、全量 fsync、六种固定修改及排空耗时。记录应用逻辑字节、对象 GET/PUT 请求和字节、FUSE 计数/直方图、Redis、CPU、磁盘和网络。单 Agent 的任务 P99 是该次任务时间，不当作高并发尾延迟。
- **正确性**：导入后验父树；第一轮校准和正式轮均重挂载完整读取父、子，核对事前独立生成的期望树。后五轮只缩减计时后的内容检查，仍验全量路径、父目录指纹和全部改动；计时操作相同。每轮保留两次间隔至少 1 秒的原生零债务、正常卸载、原 PID 退出、slot 空和逐成员归档校验。六轮 `off/on/on/off/off/on` 按原规则比较 makespan 和任务 P99，门槛 5%。

现在只生成草稿。源回执、计划、二进制、指标定义和 A 组原物理资源都已绑定；`state.sqlite` 中此项仍 pending、无已开始阶段。草稿不会发起 SSH、挂载、Redis 检查或写 state，也没有创建云资源：

```bash
python3 draft-c4-native-flat.py \
  --coordinator-root /srv/ax-pilot-control \
  --out /srv/ax-pilot-control/preparation/c4-native-flat-draft-02/plan.draft.json
```

云上已核草稿：`/srv/ax-pilot-control/preparation/c4-native-flat-draft-01/plan.draft.json`，SHA256 `02ce568e2f1e6545ed934593423b29f8e59030b94acc38fd3ecdd20470b05fd6`。脚本：[draft-c4-native-flat.py](scripts/draft-c4-native-flat.py)。它输出的是 `reviewed=false` 的计划，不能传给正式入口。

A 组 C3 正式关闭后，才在原物理锁下确认现行 collector、空 Redis DB、新目录和容量；重新核当前分支与冻结二进制，刷新 A 的 runtime 和硬件绑定。使用含 C4 卸载退出竞态修复和缩减冷验的冻结代码，保留已有计时逻辑；勿直接用旧 code15 请求重现 B 组已查明的归档问题。导入前后量容量，必须容纳额外 7 份完整拷贝并留 20% 余量。只复用原 A 的 client、metadata、objects 和协调者；失败保留现场，不重跑抢位。可执行请求及收尾见 [C4 执行与观测](C4-OBSERVE.zh.md)。

## 交接后启动

协调节点已经核对 `/srv/ax-pilot-control/preparation/c4-native-flat-run-02/request.staged.json`，SHA256 `7125cd10b0b95927253bdfa849a98439ce76ba207317a361870a14965571deca`。`stage-check.json` 证明 173 个文件按清单逐项通过 SHA256 校验，计时函数与原 C4 相同；A 的工作节点还未接触。冻结目录为 `/srv/ax-pilot-control/code-17`，只在协调节点安装。相对旧 code16，只改归档时的 slot 等待：在原 10 秒内等进程退出，仍要求 slot 空且原进程全部消失，不向任何进程发信号。实跑入口是 [continue-c4-native-flat.py](scripts/continue-c4-native-flat.py)。

先确认原 A 组 C3 成功完成，以及本批前置归档已结束。保留原草稿，另写 `request.reviewed.json`：

| 字段 | 填入 |
| --- | --- |
| `reviewed` / `database` | `true`；声明一个新的 20–255 号 Redis DB，脚本现场复核为空，勿复用 C3 的 72 号 |
| `latest` | 当场运行 `verify-latest.py`，把当前 BrewFS 与 RustFS 分支 tip 与原 build manifest 校验，填返回的实际回执路径及 SHA256；需在 5 分钟内启动 |
| `c3.unit` / `c3.invocation` | `ax-pilot-pa-c3-flat-after-base-02` 与原 invocation `81e67d4c0cd84f66aae276eb5bfce73b`，由 `systemctl show` 实际核对 |
| `c3.result` / `c3.closing` | `parallel-control-01/one-cell-runs/16f4ac389d2a2da6-r1-native/result.json` 与 `parallel-control-01/cells/16f4ac389d2a2da6-r1-native.close.json` 的绝对路径及实际 SHA256 |
| `c3.run_request` | 原 `preparation/c3-flat-native-01/continuation-02/run.request.json` 的绝对路径及实际 SHA256，必须匹配该运行的 `started.json` |
| `c3.retention_decision` | `verified-before-c4`（另附已通过的 `c3.retention_verification` 路径及 SHA256），或明确决定在 C4 期间原地保留 C3 时用 `preserve-local-through-c4` |

如启动前新增了阶段数据库，按协调节点现状更新 `other_lease_controls`；入口会拒绝漏列。确认上述回执后，在协调节点执行：

```bash
root=/srv/ax-pilot-control
stage=$root/preparation/c4-native-flat-run-02
request=$stage/request.reviewed.json
request_sha=$(sha256sum "$request" | cut -d ' ' -f 1)
systemd-run --unit=ax-pilot-pa-c4-flat-native-01 \
  --property=RemainAfterExit=yes --property=WorkingDirectory="$root/code-17" \
  /usr/bin/python3 "$stage/continue-c4-native-flat.py" run \
  --runtime-repo "$root/code-17" --request "$request" --sha256 "$request_sha"
```

原 C3 未退出、零债务或卸载证据不齐、任一相交资源仍有 lease 时，入口在连接 A 工作节点前退出。通过后才核对 Redis、slot、原 RustFS 和末轮 collector，部署冻结代码并启动一次 C4；输出固定写入 `execution-01`。该目录不得重用。六轮观测门槛若失败，不启动正式轮。

本项只验 Flat 完整拷贝。`workspace-native-v2` 的空派生、Packed 的 C3 冷读，以及 OpenCode 迁移功能预演，各有原来的证据，均不计入本次 C4。
