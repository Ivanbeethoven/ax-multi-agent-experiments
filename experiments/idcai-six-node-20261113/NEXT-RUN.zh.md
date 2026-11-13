# 六台 IDCAI：下次怎么跑

适用于现有六台机器，控制端用 PowerShell 7、OpenSSH 和 WSL Ubuntu 24.04。实验在服务器执行，不调用模型。脚本里的服务器根目录统一为 `/srv/ax-idcai-20261113`。

## 固定环境

**先落实 [RustFS 的 nofile 配置](../../infra/rustfs/NOFILE.zh.md)，四台实际进程均核为 `1048576:1048576` 后再启动实验。现有脚本还没有自动设置和拦截检查；登录终端 `ulimit -n` 或服务 health 正常均不能替代。**

| 角色 | SSH 别名 | 内网地址 |
|---|---|---|
| 编译、源客户端 | `idcai-jump` | `10.77.13.11` |
| 迁移目标 | `idcai-client2` | `10.77.13.12` |
| RustFS 四节点 | `idcai-obj1` 至 `idcai-obj4` | `10.77.13.21` 至 `.24` |

RustFS 每台使用一块现有 SSD，入口为 `.21:19000`；Redis 在 `.24:16379`，AOF `always`。所有业务流量和二进制分发走 WireGuard。运行需要已有 SSH 密钥和 `/srv/ax-idcai-20261113/private/c4-ssh.conf`；密钥和服务口令不放进仓库。

本机 SSH 的七个公网别名已用 `36.133.158.119`，端口保持原值；各别名用 `HostKeyAlias [yun.1617k.com]:<原端口>` 继续核验既有主机密钥。其余五台仍通过跳板访问 WireGuard 地址。若域名被代理解析到虚拟地址，先核连接，不关闭主机密钥检查，也不把 SSH 断开当成服务退出。脚本握手等待 30 秒；连接失败会保留本轮状态，不自动重放负载。

本批 AX 固定 `86f704be`，BrewFS 固定 `8a215a65`，RustFS 固定 `f4ed2025`，JuiceFS 为 `1.4.2`。本目录脚本复现这一批。新版本先在服务器编译和验证，再建立新批次；正在比较的几组共用同一份二进制，不能中途替换。

`build-brewfs-upstream.sh <完整提交>` 使用 `/srv/.../src/brewfs-<提交>` 中的干净源码和已有 Rust 1.99 工具链；`distribute-brewfs-release.sh <完整提交>` 在 A366 安装后直传 A355。两台分别验归档、二进制 SHA-256 和命令启动。BrewFS 这版的 feature 是 `workspace`，生成器是 `packed_snapshot_fixture`，卷格式是 `flat`/`workspace`；旧的 `*-v1` 配置和 `packed_v3_snapshot_fixture` 不能照搬。

## 输入和运行器

先复用服务器现有输入，不重复生成或下载：

| 内容 | 服务器路径（根目录之下） | 验证 |
|---|---|---|
| 完整 pytest trace | `input/pytest-posix/pytest-full-02` | 1,747,109 回调；events SHA-256 为 `0300a208c3fff9bbe125d40e2214f46d459d7cad3b86141e1960a8cf9384c45f` |
| C3/C4 两棵独立文件树 | `input/pilot-100k-001` | 各 100,000 × 4 KiB；核 `input-set.json` 和两份树清单 |
| 迁移输入 | `input/c4-fs-checkpoint-894` | 894 项；核 `checkpoint.json` 和 `install.json` |

如果需要重建十万文件输入，在跳板使用 `python3 prepare-idcai-inputs.py --out <新的绝对目录>`。它调用固定 AX 生成器，完整核内容、路径和权限，不覆盖现有目录。C3/C4 执行器目前读取固定的 `input/pilot-100k-001`；更换输入位置时同步修改配置和适配器，重新生成输入回执。

trace 下载入口是 AX 的 `scenarios/k8s-containers/scripts/download_archives.py`：`--source oss --cases pytest-full-02 --out <归档目录>`。IDCAI 使用公网 OSS 地址，不加杭州 ECS 专用的 `--internal`。归档 SHA-256 应为 `9953d90517eaf520814150f80abccb11f9d74b7ac84ecc56fa394a5f8495aa3d`。解包后按清单检查权限；已有修复由 `restore-performance-trace.py` 在新副本中恢复，原归档不改。

执行器位于 `src/ax-idcai-current-v1`。重建时从固定 AX 提交建立独立 worktree，把 `prepare-shared-workloads.py`、`adapt-current-brewfs.py`、`idcai_move_client.py`、`idcai_fork_operations.py` 放在同一目录，然后依次执行：

```bash
python3 prepare-shared-workloads.py /srv/ax-idcai-20261113/src/ax-idcai-current-v1
python3 adapt-current-brewfs.py /srv/ax-idcai-20261113/src/ax-idcai-current-v1
```

这两个步骤只对干净副本执行一次。检查原 `src/ax`、版本目录中的 BrewFS/生成器，以及 `bin/` 中的 JuiceFS、Rust 回放器、小文件工具均存在；`build-smallfiles-checkers.sh` 可在服务器重建并行导入/校验工具。C3/C4 使用 16 线程核全树，不缩成抽样。

## 启动和查看

以下命令在本目录执行。整批只启动一个控制器；共享后端的导入、测量和归档串行。

```powershell
./scripts/check-private-network.ps1
./scripts/run-current-campaign.ps1 -Campaign brewfs-8a215a6-002

# 另一个窗口查看当前运行编号。
./scripts/get-performance-status.ps1 -Run ax-four-XXXXXXXXXXXX -Case c2
./scripts/inspect-live-resources.ps1 -Run ax-four-XXXXXXXXXXXX
```

队列为 C2 三组完整回放 → C1 九项 I/O → 三种 B/q 检查 → C4 原地/跨机恢复 → C3 两组十万文件 → JuiceFS clone → BrewFS fork。C1 只跳过 JuiceFS 混合读写。C4 两组都核继承、六类修改、父树不变和完整冷重挂。C2 超时为 9,000 秒，回放时间不包含输入准备和校验。

`receipts/<批次名>/` 保存已结束阶段与运行编号。重复使用批次名会跳过已有阶段记录，包括失败项；不等于自动补跑失败。控制器断开后先用状态脚本确认原进程是否仍在执行；不要同时重启第二份整批任务。

`-StopAfter c1-layout-block-only` 可在指定阶段收尾归档后结束本次控制器，之后用相同批次名继续。C4/C3 自动启用 `-ServiceLogs`，保留脱敏后的 RustFS、collector、Redis 日志及哈希；单项补跑也加这一参数。首次启用或日志配置变化必须记在批次中，同一对照的各臂使用一致配置。

`environment-notes.json` 记录用户已说明的共享机器情况，启动时复制为本轮 `execution-context.json`。A355 正在运行其他测试；跨机恢复结果用于功能验收，计时附带整机资源样本。下轮确认机器独占后再更新该说明，不静默删除，也不停止其他人的服务或清空整机缓存。

## 补跑、保存和收尾

确认原任务已结束并完成收尾后，单独补失败项，保留原失败回执。例：

```powershell
./scripts/qualify-performance-backend.ps1 -Workloads c2 -OnlyArm c2/juicefs -TraceTimeout 9000
./scripts/qualify-performance-backend.ps1 -Workloads c3 -OnlyArm c3/brewfs-workspace

# 以下仅对已结束的运行执行；整批控制器本身会执行这些步骤。
./scripts/close-performance-attempt.ps1 -Run ax-four-XXXXXXXXXXXX
./scripts/audit-observers.ps1 -Run ax-four-XXXXXXXXXXXX
./scripts/archive-performance-attempt.ps1 -Run ax-four-XXXXXXXXXXXX
./scripts/archive-c2.ps1 -Run ax-four-XXXXXXXXXXXX -OnlyArm juicefs
```

自动收尾和单独收尾均先用 `assert-run-idle.ps1` 只读检查六台的测试进程和挂载；任何一台仍忙或连接检查失败，就不停止后端或采集器。SSH 断开不能当作远端任务已结束。清理只针对该运行的服务和容器，不按名称模糊删除数据、不格式化磁盘。已有归档保留，重复归档会拒绝覆盖。

每台的原始归档保存在 `evidence-archives/<运行编号>/<角色>.tar.gz`；本机 `receipts/<运行编号>/raw-archives.json` 保存路径、大小、SHA-256。原始指标和 trace 大包留在服务器，仓库放脚本、小型结果和索引。

C4 fork/clone 的 `receipt.json` 包含完整十万文件树，保留在服务器原始归档中。`compact-native-receipt.py` 在服务器另生成摘要，控制端只取 `.summary.json`：保留检查结论和计时，把大树替换为文件数和树哈希，并记录原回执的大小与 SHA-256。摘要不能替代逐文件审查；需要时按归档索引取原回执核对。

三组 C2 的运行编号分别传给 `scripts/summarize-c2.py`；使用 `--arm-run brewfs-workspace=receipts/<编号>`、`--arm-run juicefs=receipts/<编号>` 指定另外两组。它核对输入和二进制、实际操作/字节、终态、冷重挂、lower 不变与清理证据。部分回放不能算通过；失败记录保留实际进度。

采集每 5 秒一次，闭合后检查样本缺失和断档。新采集器另外保存客户端进程的 `/proc`，迁移目标靠 `AX_IDCAI_RUN` 对应本轮。本轮 C2 没有持续的客户端逐进程 CPU 记录；正式重复、观测开销校准和多 Agent 扩展尚未完成。
