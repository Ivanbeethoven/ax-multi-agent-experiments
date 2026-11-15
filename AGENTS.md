# 当前任务

新一轮直连实验先读 docs/HANDOFF-DIRECT-NETWORK.md、docs/NETWORK.md、configs/campaign-direct.json 和 configs/network.json。执行范围为 C1～C4，结果放 results/cluster-direct/run-NNN/；服务数据使用 eth0 直连。已有分组轮次继续按各自 campaign 和结果目录保存。

再读 README.md、docs/SETUP.md、docs/MOUNTS.md 和 docs/METRICS.md，以及对应 C 的 README.md 与 experiment.json。

直连轮次分组执行 C1～C4 的系统对照、消融和参数实验。普通任务使用 BrewFS Native Overlay；Packed 只用于只读大量小文件。对照是原生 JuiceFS 和 JuiceFS Overlay，CephFS 留待后续。

C4 主对照采用共享 Native base、共享桶的离线合并与 POSIX 三方工具，先完成四节点 RustFS 上的准备验证。实现细节见 docs/HANDOFF-C4.md；直连轮次的结果放入其统一目录下的 C4/。

## 参数与实现

- configs/campaign-direct.json 定义直连轮次范围，configs/cluster.json 定义六机资源，configs/mount-profiles.json 定义缓存和写入参数。每个 C 的 experiment.json 定义条件；configs/comparisons.json 定义技术比较。configs/campaign.json 与 campaign-c4.json 保留各自分组轮次范围。
- 先同步指定分支，构建一次并分发，保存实际源码提交和构建参数。配对重复内固定版本。BrewFS 为 brewfs/brewfs main；RustFS 为 Ivanbeethoven/rustfs feat/dynamic-block-size-rebased；JuiceFS Overlay 为 Ivanbeethoven/juicefs codex/lazy-workspace-fork-20261113。
- A366 负责构建和协调；A355 Redis；A672/A601/A635/A368 四节点一盘 RustFS。六台都可运行客户端。使用 idcai-60001/60525/60103/60439/60533/60555 SSH 别名并复用连接。
- 本轮最多 128 个活动工作区；每个工作区 4 GiB 内存、2 GiB 磁盘缓存。按主机汇总客户端和常驻服务资源。192 并发扩展单独运行。
- B、q、应用请求大小和文件系统 chunk 分开记录。q 取实际持久化对象布局，不根据 B 推填。
- 同机共享缓存需实现跨挂载读取复用，写入缓存保持私有；不能直接共用有独占锁的目录。
- 用户禁止计算、生成、比较或验证任何哈希及校验和，也禁止创建相关文件。实际内容直接比较；复用旧脚本前检查执行路径。
- 实现并测试缺少的适配；当前计划导出命令不负责启动远端性能任务。手动实验命令与必要修补沉淀为可复用脚本。

## 运行与保存

- 直连结果放 results/cluster-direct/run-NNN/，服务器放 /srv/ax-cluster/runs/direct/run-NNN/。已有编号不覆盖，raw 结果暂不推送。
- 同一后端一次一个计时条件，系统顺序按重复轮换，三次重复保留全部结果。编译、下载、清理与计时分开。
- 实验开始前保存实际配置并确认原始指标已产生。应用完成、上传完成、新挂载读回分别计时。详细字段和目录见 docs/METRICS.md。
- 清理只处理本轮登记的进程、挂载、对象前缀和目录。先取回原始数据，保留其它会话的资源、公共输入和历史结果。
- 不调用云 API 租新机器，不格式化现有盘。性能条件不能用缩小后的功能检查代替。
- 本机使用 PowerShell 7；SSH 远端按实际 Linux shell 执行，复杂命令优先传脚本。
- 改动前重新读取当前文件，保留用户与其它会话的修改。当前仓库规划由本会话编辑，BrewFS 合并实现会话不修改此目录。

## 目录

experiments/ 只放 C1～C4；共用说明放 docs/，配置放 configs/，环境放 infra/，输入工具放 datasets/，原始结果放 results/。旧计划、日期命名的诊断和历史运行记录在 archive/experiments/，按原记录解释。

修改后运行 tests/cluster 的针对性检查；功能代码还要完成相应实际运行测试。不要调用会生成校验和的旧仓库检查脚本。
