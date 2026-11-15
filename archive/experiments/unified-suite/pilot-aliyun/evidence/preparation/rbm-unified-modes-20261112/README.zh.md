# 统一构建：rbm 最小模式验证

2026-11-12，在同一台 rbm 串行运行。BrewFS 为上游 main ca07159 的干净源码；保留 default features，并启用 workspace-overlay、observability、rustfs-ec-block-size-hint。构建成功，用时 45m01s。[构建清单](build.json)保存二进制 SHA，[Cargo 指纹](brewfs-cargo-fingerprint.json)记录实际 feature 集。

26 个模式最终为 **16 passed、1 failed、9 blocked**；原始批次加一次 JuiceFS C2 重跑，共 27 次尝试。[最终状态](latest-status.json)关联当前回执，[原始批次](summary.json)保留首次结果。

| 子项 | 结果 | 验证范围 |
| --- | --- | --- |
| C1 I/O 与布局模式 | 9 通过 | 两文件系统、enabled/off、4 档固定 hint、block-only，读写、fsync、卸载、新缓存重挂及 SHA256 |
| C2 合成回放 | 4 通过、2 阻塞 | 140 回调；basic/full/JuiceFS/dynamic-off 均严格校验，98,442 读字节、98,365 写字节，剩余句柄 0；packed-off、all-off 未实现 |
| C2 Overlay 运行开销 | 2 阻塞 | 同引擎开关与原生 lower 热态前提未验收 |
| C3 小文件 | 2 通过、1 阻塞 | 128×4 KiB；BrewFS/JuiceFS 初始校验、固定读写、重挂核修改文件；packed-off 未实现 |
| C4 原生派生 | JuiceFS 通过，BrewFS 失败 | 同一两文件 77 字节；子读、覆盖和新建、父内容校验、子重挂 |
| C4 干净迁移 | 4 阻塞 | 两产品 stay/move；当前最小执行器未接入共享工作区、源目标切换与会话续跑 |

BrewFS fork 的子工作区没有 Packed lower 绑定，初次读回为空。子写及重挂可保存，父内容未变；因为没有读到继承文件，不能将同名写记为覆盖继承文件的隔离通过。未改写这一失败，也未使用完整复制补结果。

JuiceFS C2 首轮被目录检查误读根目录 .accesslog 虚拟流卡住，尚未开始 Rust 回放。已保留[干预记录](C2-replay-juicefs/intervention.json)和首次失败回执；停止该探针并清理后端与挂载，修正检查器不读取根控制流、不遍历 .trash，回归测试用 FIFO 确认不会阻塞。修复后[独立重跑通过](C2-replay-juicefs-rerun-01/receipt.json)。普通隐藏文件和子目录里的同名用户文件仍检查。

这些结果是功能探针：C1 不证明实际 B/q 已改变；C2 不是完整 Agent trace；C3 不是 10 万规模；单机重挂不等于跨机迁移。RustFS 使用同机私有容器和四目录，只用于功能验证，不能替代正式四节点性能实验。未新增云机；测试服务和挂载均已清理。原始日志和产物保留于 rbm:/var/tmp/ax-mode-suite-20261112。

运行入口见[说明](../../../RBM-MINIMAL.zh.md)。BrewFS 源码本轮无需为运行 flags 修改：已支持的格式和 hint 被传入原生命令，缺失功能保留 blocked。之前停止的 v1 修复草稿未进入这次构建或推送。
