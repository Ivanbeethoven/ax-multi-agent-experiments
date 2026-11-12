# C4 历史 On1：真实规模折叠与独立读回

2026-11-12。只启动 A 池 objects，用原采样盘处理已关闭的 `c4-a-flat-on1`。没有启动 Redis、RustFS 或客户端，也没有跑新负载。本记录只证明归档准备成功；正式结果仍为 14/28。

- 折叠前，原 2,954 份 `sample.json` 均与历史 `finished.json` 中的 SHA-256 一致。原始拆包和原 `finished.json` 已另存协调者，逐件校验通过。见 [预检](preflight.json) 与 [副本校验](backups-verified.json)。
- [工具回执](fold-real-03.json)：2,954 个样本、132,930 个样本成员，输出 23,497,612,644 B。工具 [退出码为 0](exit.json)，用时 10 分 10.71 秒，峰值 RSS 112,584 KiB。
- [外层脚本退出码为 1](apply-exit.json)：它要求整个顶层少于 10 个条目，实际有 9 个观察者必需文件、3 个审计或日志文件、1 个历史 socket。原始 [错误](apply.stderr) 保留。没有重跑或删除这几个审计文件。
- 停机前 [独立读回](independent-readback.txt)：完整读取单流，字节数和 SHA-256 与工具回执及折叠后的 `finished.json` 一致；0 个 `sample-*` 目录。原始树的全量可读副本在协调者，折叠流在已卸载的原采样盘；完整 `finished` 回执另存本机和协调者。

协调者路径：`/srv/ax-pilot-control/preparation/a-c4-observer-fold-real-03/`。对象节点路径：`/srv/ax-c4-observer-scratch/o/c4-a-flat-on1/`。这不是完整 C4 校准回执。

从启动请求到确认停机约 19.6 分钟，按该节点新增计算费 ¥1.8/h 估算约 **¥0.59**，不含已存在的盘、跳板与公网账单。无新增资源。 [停机 API](stop-api.json) 返回后，[独立查询](cloud-stopped.json) 确认六台实验节点均为 `Stopped/StopCharging`，原跳板仍运行。

## 历史 objects 标准归档

原 On1 的 [协调者盘点](historical-on1-inventory.json) 找到 client、metadata 的原始包，两包 SHA-256 均与当时的回执一致。原工作、冷验、零上传债务和正常退出记录仍在。原 continuation 与最后一条归档命令保留退出码 1，错误为 `unbounded internal manifest`。

另用一次短窗口唤醒同一台 objects：只读挂载同一块采样盘（`ro,noload`），不启动存储服务。把 23.5 GB 单流、原 `finished`、探针、闭合和审计文件打成原格式的归档。冻结校验器逐成员验证通过：**18 个文件，644,436,975 B**，SHA-256 为 `9ac03e19a4f1e1914fa68fed2fe67cb307bd511decb33540c522df93342d13c2`。原拒收包及旧失败记录均未改写。[原生校验](historical-objects-pack/objects-check.json)；[打包来源](historical-objects-pack/objects-pack.json)；[协调者读回](historical-objects-pack/retained.json)。

完整归档已留在协调者 `/srv/ax-pilot-control/preparation/a-c4-on1-historical-archive-01/objects.tar.gz`。第二次窗口从启动到确认停机不到 6 分钟，新增计算费约 **¥0.18**，两次窗口合计约 **¥0.77**。11:17 [六台实验节点停机复核](historical-objects-pack/cloud-stopped.json)通过。未形成正式结果，也未启动任何新测量。

## 可审阅的离线恢复请求

[请求草案](recovery-request.draft.json)固定原 On1 请求、原 Off1、原 plan、三个角色包及协调者副本证明。已在协调者实际运行 `recover-c4-on1-archive.py --validate-only`：[只读回执](recovery-validation.json)确认原 On1 仍为退出码 1，三份原始归档 SHA-256 匹配，恢复预计占 25.69 GB 和 178,084 个 inode，当前数据盘可用 82.69 GB，仍能保留 20%。没有生成恢复目录。该检查不代替解包后的三角色观测、工作结果与冷验审计。

审阅通过后，在协调者另存审阅件；原草案、旧失败和旧试验目录保留。使用原代码副本中的 `recovery-code/experiments/unified-suite/pilot-aliyun/scripts/recover-c4-on1-archive.py --request <审阅件绝对路径> --sha256 <审阅件SHA256>`，输出新 `recovered-trial`。无需开实验节点。该回执仍只是原采集器的 On1 校准输入；更换为直接写 NDJSON 后需单独重新校准。
