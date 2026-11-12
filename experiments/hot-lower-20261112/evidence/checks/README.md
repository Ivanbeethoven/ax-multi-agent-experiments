# 本地正确性检查，2026-11-12

源码基准：`ca07159f3da4b670e4aeb878faef928e69bc1634` 加 `source-tested.patch`。
Rust/Cargo 1.99.0，WSL Ubuntu 24.04。未使用云资源。

| 检查 | 结果 | 原始日志 |
|---|---|---|
| 新增 Overlay 路径测试 | 5 通过 | check-focused.log |
| 行缓存、容量、失效、帧缓存 | 10 通过 | cache-tests-01.log |
| 默认 workspace 测试，lib + bins | 1,355 通过，265 原有 ignored | ci-default-and-interrupted-extra.log |
| Overlay 完整库测试 | 2,788 通过，485 原有 ignored | ci-remaining-01.log |
| workspace check/build、两种 FUSE runtime check、3 项 shell 测试、4 项 shell 语法检查 | 通过 | ci-default-and-interrupted-extra.log |
| 默认和 Overlay 的 Clippy，workspace + all-targets，警告视为错误 | 通过 | ci-remaining-01.log |
| fmt、git diff --check | 通过 | ci-remaining-01.log |

`ignored` 沿用原有测试声明，主要要求独立 Redis、TiKV、对象存储、FUSE 或 Btrfs
测试环境；本轮未更改这些声明。实际 Redis/RustFS/FUSE 路径由后续本地配对实验验证。

默认 workspace 检查完整执行并通过。之后额外开启 Overlay 的 workspace 测试触发
了一次重复编译，已主动中止，原日志保留 `Terminated`。改用完整的 Overlay 库测试
后复用了已生成的测试二进制，3,273 个用例全部进入运行或原有 ignored 状态，没有
使用名称过滤。Clippy 的两组检查仍覆盖整个 workspace 和所有 targets。

`source-tested.patch` 由 Windows Git 导出。WSL Git 未配置对应的 LFS clean filter，
会把相同的预编译 xfstests 包误列为修改；规范化补丁保留实际代码、测试和文档变更。
原版 release 编译成功，但编译后的辅助脚本因运行中修改而解析失败；其二进制已
单独保存并校验 SHA，见 upstream-artifacts.json。没有用该辅助脚本的退出状态冒充
整体成功。这些是准备阶段记录，不是性能结论。
