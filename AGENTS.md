# 当前任务

先读 docs/PLAN-REVIEW.md 与 experiments/README.md。用户已确认四组方案；完成配置、脚本与实际运行检查后推送 main 并启动。

## 实验与代码

- 四组为基础 fio、fork 与 Overlay、Packed、多 Agent trace 回放。
- Dynamic 开启访问修改版 RustFS 9000，关闭访问原版 1.0.5 的 19000；各自采集 9464/19464 指标，端点见 configs/rustfs-backends.json。
- Dynamic 比较放在第 1、3、4 组；第 4 组只在 64 个 Agent 时增加关闭条件。
- 复用 bench/cluster、infra/idcai、infra/rustfs 和 tools 中的现有实现。旧套件及对应测试位于 archive/legacy-code。
- 输入目录见 configs/trace-inputs.json。完整回放使用 tools/full-fuse-replay。
- 本轮每个条件运行一次，共 52 项。入口为 infra/idcai/start_suite.py；状态由 runtime/status_suite.py 单次查询。
- 用户禁止计算、生成、比较或验证哈希与校验和。内容采用直接字节比较。

## 机器与运行

- 使用现有六台机器，数据访问走 eth0 直连。SSH 使用 idcai-60001/60525/60103/60439/60533/60555，控制连接数并复用连接。
- A366 负责构建和协调，A355 提供 Redis；A672/A601/A635/A368 提供四节点一盘 RustFS。
- 准备、挂载和监控入口见 docs/SETUP.md、docs/RUNNING.md、docs/MOUNTS.md、docs/METRICS.md。
- 同一性能比较组依次执行。各系统固定输入、种子和总资源预算，记录实际生效配置。
- 原始结果以运行编号保存，旧结果进入 archive/results。服务器数据在整轮完成后取回本地。
- 清理只处理本轮登记的资源；保留公共输入和其它会话数据。不开新机器，不格式化已有磁盘。

## 修改与检查

- 本机使用 PowerShell 7；远程命令按 Linux 的实际解释器执行。
- 修改前重读文件，保留用户和其它会话的修改。
- 核心检查使用 tests/cluster 及当前工具对应测试。历史检查代码保存在 archive 中。
- 方案确认并启动后，收到进度提醒时检查当前任务；成功后继续下一项，有错误则修复并验证。
