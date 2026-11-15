# C4 执行交接 prompt

直连轮次先读 [直连交接](HANDOFF-DIRECT-NETWORK.md)，网络使用 [network.json](../configs/network.json)，结果放直连轮次的 `C4/`。本文提供 C4 的接口与操作细节；单独执行 C4 时使用下述分组目录。

进入 `C:/Users/HXY/.cache/ax-agent-cluster-plan-20261114`，完成 fork、私有增量和三方合并的 C4 实验。先阅读：

1. `AGENTS.md`、`experiments/c4-fork/README.md`、`experiments/c4-fork/experiment.json`、`experiments/c4-fork/adapters.json`。
2. `configs/campaign-c4.json`、`configs/cluster.json`、`configs/mount-profiles.json`、`configs/cache-sharing.json`。
3. `docs/SETUP.md`、`docs/MOUNTS.md`、`docs/METRICS.md`、`docs/ABLATIONS.md`。
4. `bench/cluster/WORKSPACE_MERGE.md` 和 `bench/cluster/workspace_merge.py`。

## 目录和计划

本地首轮 `results/cluster-c4/run-001/`，服务器 `/srv/ax-cluster/runs/c4/run-001/`；已有编号顺延。A366 汇总，其余节点在同一路径下写各自 `hosts/<name>/`。

```powershell
$Py = 'C:/Users/HXY/AppData/Roaming/uv/python/cpython-3.14-windows-x86_64-none/python.exe'
& $Py -X utf8 -m unittest discover -s tests/cluster -p test_c4_design.py -v
& $Py -X utf8 -m unittest discover -s tests -p test_workspace_merge.py -v
& $Py -X utf8 -m bench.cluster.plan --group C4 --out results/cluster-c4/run-001/plan.json
```

默认 30 个条件、228 次系统运行，含三次重复；百万文件属于独立扩展。本轮只做 C4，C1～C3 的配置与结果保持原样。使用同一四节点 RustFS/Redis 后端时，安排独立测量时段。

## 软件与后端

使用既有别名：A366 `idcai-60001`，A355 `idcai-60525`，四台 RustFS 为 `idcai-60103/60439/60533/60555`。复用连接，不新增服务器。

BrewFS 合并实现为 `codex/workspace-merge` 的 `aacfc6db`，源码目录 `C:/Users/HXY/.cache/brewfs-cluster-main-efd9c902`；AX 接入提交 `0bf2d0d7` 的脚本已复制到当前规划目录。检查最新 main 是否包含该实现：包含时使用该 main；尚未包含时在独立构建分支加入该实现并记录全部来源，完成相关测试。A366 编译一次 release，分发同一个二进制，保留当前其它任务版本。

POSIX 工具位于上述 BrewFS 源码的 `tools/experiments/workspace_merge/merge_posix.py`，JuiceFS、JuiceFS Overlay 和 BrewFS 的 POSIX 方法比较使用同一份文件。先运行其 `test_merge_posix.py`。原功能结果在 `tools/experiments/workspace_merge/results/redis-fuse.json`，它对应 Redis/local-fs；本轮性能数据使用四节点一盘 RustFS。

## 先准备共享 Native 父版本

BrewFS 合并需要相同不可变 Native base、相同对象桶以及 depth=2 的 writable head。准备工具通过 catalog API 建立真实父文件树和共享桶工作区，正确分配 inode/slice 并保存数据引用。普通 S3 fork 会创建独立 upper bucket，不能直接用于本组合并；不要仅修改已有工作区的桶字段。

创建 `workspaces.json`，逐项保存 volume、namespace、base revision、base layer、target/child UUID、head layer、upper bucket、数据端点和挂载目录。确认父文件确实在共同 base 中，child 私有层仅含随后写入的修改。

先用 1,000 个 4 KiB 文件、一个 child、10 项修改，在 RustFS 上完成：挂载读取、私有修改、上传排空、卸载、merge、target 新缓存读取、同操作重试、discard child 后回收、target 再次冷重挂。再做双方独立修改与冲突各一例。修改配置或适配时保留最终可复用准备脚本。

共享桶中保留 base、target 与 child 引用的全部对象；回收 child 采用 discard 和引用管理流程。整桶删除只在本组全部工作区、读取者结束之后进行。

## 命令和阶段

先保存操作 UUID 文件，重试复用原文件。适配器已负责写文件并同步，然后执行 merge。以下为 A366 实验仓库内的命令结构，各参数取自本轮清单：

```text
python3 -m bench.cluster.workspace_merge --binary <release-binary> --meta-url <redis-url> --namespace <namespace> --child <child-uuid> --target <target-uuid> --operation-file <private-operation-file>
python3 <brewfs-source>/tools/experiments/workspace_merge/merge_posix.py <base-mount> <child-mount> <target-mount>
```

Native 调用前停止写入、等待实际上传完成、正常卸载 child 和 target、确认原进程退出与 lease 释放。POSIX 工具在停写且状态稳定的挂载上运行。两者的业务停顿都从停止新写入开始，到 target 可读结束；命令自身时间另列。

合并命令退出后等待持久完成，再用新进程、新缓存读取结果。首次可用读取包含一处已修改文件、一处未修改文件和目录可见性；完整内容与属性检查在其后单独计时。上传、合并和读回三个阶段的 S3 计数分别保存。

每个正常重复使用新工作区和新 operation 文件。正常合并输出、重复提交输出、丢失响应后的查询结果分别保存；相同操作的返回缓存不能计为一次新的合并性能。Native 有冲突时 AX 返回 2，调用/协议错误返回 1。预期冲突记录为功能检查，独立于成功合并吞吐。

## 负载、消融与资源

按 JSON 顺序执行并轮换系统顺序，每项重复三次。先跑 fork 与局部修改，再跑空合并、规模/修改量曲线、target 增量曲线，最后做 4/16/64 个 child 归并。所有 child 来自共同 base；归并到同一 target，target 私有记录逐次积累。

随机种子、修改路径、偏移和内容在系统间一致。公共性能测试仅使用三个系统与 POSIX 工具均支持的操作；硬链接、xattr/ACL、原子提交与重试使用 Native 专项。POSIX 方法在 BrewFS 上另跑一组，帮助区分整个合并方法与底层文件 I/O 的差异。

技术消融依 `configs/comparisons.json`：目录记录共享、范围增量、行缓存、同机缓存，以及差异枚举/完整遍历 × 对象引用/文件重写四组。实现缺少独立开关时先接入并测试，再运行对应条件，状态写 `pending-feature`；可执行主实验照常完成。各开关的实际支持状态写入 `capabilities.json`，不要传不存在的 CLI 参数。

最大 64 个 child 分布 A366/A355/A672/A601/A635/A368 为 16/11/6/13/13/5，base 和 target 在 A366；总计最多 66 个挂载。每挂载 4 GiB 内存、2 GiB 磁盘缓存。子工作区未同时使用时及时卸载；冷读复用已释放槽位。相同后端一次只运行一个计时条件。

## 原始数据和汇总

保存每个命令的主机、参数、开始/结束时间、退出码、stdout/stderr；Native JSON 保留 `scanned_records/applied_records/conflicts/target_sequence`，POSIX JSON 保留枚举、比较与复制字节、各阶段时间。补充 Redis 全部实际命令与字节、S3 请求与字节、cgroup/宿主资源和四台对象盘指标。

`scanned_records` 只统计私有行，不含 base 点查；另保存 Redis 访问总量。Native 当前没有内部分段计时字段，未增加计时前这些字段记为 `null`。父版本准备、数据持久完成、合并命令、首次可用和完整检查分别计时。

结果目录至少包含：

```text
run-001/
  README.md plan.json inventory.json versions.json capabilities.json
  workspaces.json mounts/ commands.jsonl status.json
  hosts/<host>/
  C4/<condition>/<system>/r<repeat>/
    stages.jsonl merge.stdout.json merge.stderr.log
    application.json verify.json resource.json
  summary.csv comparisons.csv figures/
```

`private/operations/` 的操作文件可能含 Redis 凭据，公开结果只复制操作 UUID、工作区 ID 和去除凭据的地址。所有内容检查使用实际字节和属性，不生成或核验哈希/校验和。

汇报按 `C4：完成 x/30 个条件，y/228 次系统运行`，同时列主实验、消融和方法比较。失败或容量超限保存实际结果，重试另建 attempt。全部原始数据取回本地、确认可读之后，清理本轮缓存、挂载和对象前缀；公共输入与其它任务资源保留。
