# 合并主线后的检查

代码：[BrewFS PR #450](https://github.com/brewfs/brewfs/pull/450)，检查源码为
`7fa274e40277123e206aa911f7f8f6b0499404e8`。它包含 #448，已合入主线
`8a215a65cad19d34fd3fd7f04029f97d09617364` 的修改。实验记录由
[AX PR #19](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/pull/19)交付，包含 #18。

本轮检查了合并后的正确性，**没有重测当前版本相对 lower-only 的性能损失**。
此前缓存优化的 35.3% / 14.1% 和合批优化的 41.5% / 43.9% 仍属于各自历史配对，
不能当作当前 Overlay 的剩余开销，也不能把它们叠加。

## 合并时改了什么

- 保留主线的按字节限制的缓存、Packed 预取、共享统计和写回修复。空区间和空目录项缓存接入同一套容量、版本和失效管理。
- 上层与命名空间缓存共用默认 64 MiB，每种缓存最多 65,536 项；默认 TTL 为 60 秒。lower 已认证帧保留默认 8 MiB。增加了小容量下空结果入缓存和释放额度的测试。
- 适配主线的 `workspace`、`packed`、`snapshot` 模块和 `packed_snapshot_fixture` 程序名。
- 将配置名改为 `BREWFS_PACKED_RETAINED_PAYLOAD_BYTES`。主线拒绝旧的版本化环境变量；第一次真实挂载检查因此失败，失败记录保留，修正后再测。
- 合批仍需要写回元数据库。每次修改的远端租约与时钟检查保留；没有加入跨操作的租约校验缓存。

## 检查结果

| 检查 | 结果 | 原始记录 |
|---|---|---|
| 合并后的默认测试 | 库 1362 项、命令行 2 项通过；266 项跳过 | [最后一次本地检查](evidence/cli-check.log) |
| 完整 Overlay 库测试 | 2849 项通过、489 项跳过 | [合并检查](evidence/ci-final.log) |
| 格式、工作区编译、运行时 feature、Clippy | 通过；默认及完整 feature 均做了 all-targets Clippy，警告作为错误 | [初轮](evidence/ci-initial.log)、[修正后](evidence/ci-final.log)、[配置名修正后](evidence/cli-check.log) |
| GitHub CI | 最终提交 `7fa274e` 的 Rust 工作流通过 | [CI 运行](https://github.com/brewfs/brewfs/actions/runs/43385563597)、[状态快照](evidence/github-ci.json) |
| 真实 Redis | 20 项集中测试通过；2 项默认跳过的 Redis 测试显式运行通过 | [Redis 日志](evidence/redis/run.log) |
| AX 测试 | 704 项中 678 项通过，26 项跳过；计量代理另 2 项通过 | [测试](evidence/ax-tests.log)、[补充检查](evidence/ax-final-check.log) |
| 历史数据 | hot-lower 的 4 份运行、合批分析的 28 份运行及文件校验和通过 | [核验日志](evidence/ax-evidence-verification.log) |
| A355 真实挂载 | 12 单元全部通过；168 次计时读取、192 次元数据修改，12 次远端重挂核验 | [汇总](evidence/a355/summary.json)、[文件校验](evidence/a355/smoke-01-verification.json)、[原始运行](evidence/a355/smoke-01/run.json) |

完整 Overlay 套件和真实 Redis 检查对应 `1081395`。随后只改了配置项名称及文档，
并重新完成默认测试、两组严格 Clippy 和 A355 挂载检查。A355 二进制来自
`1081395` 加 [两文件补丁](evidence/build/source.patch)，源码内容等于 `7fa274e`；
没有把构建时的提交号改成后来生成的提交号。

A355 是用户指定的已有空闲服务器 `idcai-client2`。本轮没有新建机器或付费资源，
Redis 和 RustFS 在 A355 上临时运行，只监听回环地址；未接入其他五台 C2 节点。
二进制使用 **debug 构建，只验证正确性**。原始耗时保留，但不据此报告性能收益。

两种输入分别为原来的两个文件，以及包含覆盖、截断、扩展、删除、新建、重命名的上层。
每种输入交换缓存开关顺序做 3 对；每个单元先预热，再读 4 轮，重复修改权限 16 次后 fsync。
随后启动不读取本地日志的远端模式进程，核对内容、目录和最终权限。全部单元的热读阶段、
元数据修改阶段 RustFS 请求及收发字节增量均为零；Redis 命令仍有记录。
最终两个临时容器、挂载及临时数据目录均已清理。
结果取回并校验后，还删除了本轮传输包和临时二进制，释放 1.60 GiB；固定镜像留作复用。
见[清理回执](evidence/a355/transfer-cleanup.json)。

## 复用与证据

常规优化构建及配对入口见[热 lower 脚本](../../hot-lower-20261112/README.zh.md)。
新版主线传 `--workspace-format workspace`，旧记录复现仍用 `workspace-v1`。
调试构建必须传 `--build-profile debug`；该选项只记录构建类型，不会把调试构建变成性能构建。

本轮实际命令：[本地检查](evidence/cli-check-command.sh)、[构建](evidence/smoke-build-command.sh)、
[A355 执行](evidence/a355/smoke-command.sh)。这些是带当时绝对路径的执行记录，复用时改输出目录，
保留同一套版本、镜像校验和独立服务；运行器拒绝覆盖已有结果。
源码、二进制及输入传输摘要见 [provenance.json](provenance.json)。归档不包含二进制和镜像。

```bash
python3 verify.py
```

初轮 Clippy 暴露了主线风格问题，已按原规则修正。后续一个包装脚本因 Git 索引变化重复编译同一套
已通过的测试，被主动终止；其退出码 101 和原日志保留。随后直接运行同一份已通过的测试二进制，
完成真实 Redis 检查。AX 首次代理测试因系统 Python 缺 boto3 失败，改用已有虚拟环境后通过。
这些失败和中断均未改写成成功。

尚未验证：当前主线相对 lower-only 的性能开销、TiKV 运行时、跨机崩溃恢复。
