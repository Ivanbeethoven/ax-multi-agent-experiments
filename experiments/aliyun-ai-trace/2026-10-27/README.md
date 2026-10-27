本轮重测已完成 `pytest-full-02` 的全部 1,747,109 次操作，使用原 Rust 回放器的 performance 模式，不设总时长上限。三组读写字节一致、剩余句柄 0、回放退出 0，云资源已释放。[完整诊断图与简短报告](summary/summary.zh.md)保留配置、准备成本、错误、费用和验收边界；这是单次开发诊断，不能作为跨主机公平对照或严格正确性证明。

| 方法 / 版本 | 当前结果 | 数据 |
|---|---|---|
| native-overlay / C4 | 完整回放 2187.17 s，读写字节一致、句柄归零、正常卸载与挂载自然退出；1048 项 probe errno 差异 | [candidate4.json](native-overlay/candidate4.json) |
| native-overlay-pack / C4 | 完整回放 1730.24 s，读写字节一致、句柄归零、正常卸载与挂载自然退出；1048 项 probe errno 差异 | [candidate4.json](native-overlay-pack/candidate4.json) |
| JuiceFS / attempt02 | 完整回放 1853.92 s，读写字节一致、句柄归零，probe errno 差异 0；本组云资源已全部释放 | [result.json](juicefs/result.json) |

JuiceFS 首次尝试被系统自动升级重启服务打断，证据见 [interruption.json](juicefs/interruption.json)。原实例暂停自动服务重启后，使用新目录完成第二次回放；不是冷宿主页缓存实验。

旧版 [C1 完整回放](native-overlay/candidate1.json)只保留为历史，不进入最终图。在其持久化状态上重新挂载并中断在途写请求的探针也自然退出，见 [candidate1-interrupted-write.json](native-overlay/candidate1-interrupted-write.json)。这没有排除先前长生命周期进程或 WSL 状态导致的挂起。

当前候选版本为 C4：源码包 SHA256 `d609a429dfce0943114717495a76c164598df36a4564768fc346156548ea2783`，基于 `e61ab89448c1482d11f5baca2a5ad448f5818213`。C4 把缓存清理与目录版本失效分离，17 项本地检查及 `git diff --check` 已通过，包括严格 Clippy。packed 库测试 2,497 项通过、451 项忽略；默认库测试 1,261 项通过、262 项忽略；相关二进制测试也通过。最终 release 构建与两配置各 16 次真实挂载短回归通过，两个挂载进程均自然退出 0。

**C4 尚未通过性能验收。** [本地两轮 compose 原始证据](compose/summary.json)保留改善与回退：纯写对顺序敏感，直接随机写额外墙钟开销待归因。命令成功不等于无性能回退；本次发布的是带限制的实验报告，不是已接受的 Rust 优化。图及报告已完成自动与人工 QA，[生成脚本和数据约定](reporting/README.md)可追溯全部数据。

原始归档、构建日志和资源清单保存在仓库忽略目录 `runs/aliyun-{native,packed,juicefs}-fix-20261027/`；私有配置和临时签名地址不随公开摘要提交。
