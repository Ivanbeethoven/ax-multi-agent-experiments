# A355：Overlay 打开、权限缓存与关闭路径

使用现有 A355、本机 Redis 和 RustFS，没有新建云资源。基线源码为 BrewFS
`03c8cc55d61473533c00a8afe13b4ef1fcd9b725`，正常 release、opt-level=3。

## 先纠正对照条件

[上一轮](../overlay-total-a355-20261113/README.zh.md)虽然两边 JSON 都写 TTL=0，
实际直接 Packed 返回 1 秒，Workspace 返回 0。原始 stats 中的
`brewfs_fuse_attr_valid_ns`、`brewfs_fuse_entry_valid_ns` 可以证明。
旧的约 2.5 倍不能当作缓存相同条件下的 Overlay 开销。

新运行器同时设置 `BREWFS_CACHE_TTL_MS=0` 和挂载配置 TTL=0，核验挂载后、首轮后、
稳态后的实际值。历史数据保留；修正对照条件不算代码优化。
同一旧二进制的公平基线（6 对、256 轮，热态每轮中位数）：

| 输入 | 直接 Packed | 原版 Overlay | 额外耗时 |
|---|---:|---:|---:|
| 2 文件，77 B | 2.564 ms | 3.918 ms | 52.8% |
| 32 文件，122,957 B | 29.186 ms | 36.263 ms | 24.2% |

这是小文件诊断，不代表完整 Agent 任务的性能损失。冷态指重新挂载且客户端没有预读
Packed；不清 RustFS 或宿主页缓存。冷态首轮与后续稳态分开统计。

## 原来约 19 ms 在哪里

按原有 TTL 条件复测 32 文件，Overlay 相对直接 Packed 每轮平均多耗时：
文件 open **15.560 ms**、fstat **1.880 ms**、close **1.085 ms**。
合计 18.525 ms，解释了此前未拆出的主要部分。open 包含路径查找、权限检查、内核与
FUSE 调度，不能全部算成权限缓存成本。逐轮各分项之和严格等于总耗时。
分解使用均值，性能表使用中位数，两者不混加。

实际 TTL 对齐后的基线中，这三项额外耗时分别为 **2.750 / 0.240 / 1.063 ms**。
因此此前那 19 ms 很大一部分来自对照的内核缓存差异，不能全部归因于 Overlay 实现。

## 候选修改

- 权限缓存命中后共享已有不可变快照，减少反复复制。仍使用原来的 64 MiB 上层缓存预算、
  有效期、租约检查及修改前后失效机制。
- 只读 open/close 先执行一次；立即完成时省去创建任务及唤醒调用者。如需等待，把同一个
  未完成操作交给后台任务，调用者取消也不会丢掉已开始的操作。权限、句柄、unlink 与关闭同步、
  排空及写句柄路径保留。
- 两版都含相同的独立 Packed 缓存接线补丁，用于预算对齐；它不是候选的提速差异。

第一轮已完成 72 个读取单元、6 个上层修改/重挂单元。热态每轮中位数：

| 输入 | 原版 Overlay | 第一轮候选 | 直接 Packed | 候选/原版 − 1 | 候选/直接 Packed − 1 |
|---|---:|---:|---:|---:|---:|
| 2 文件 | 3.955 ms | 3.858 ms | 2.562 ms | −2.5% | +50.6% |
| 32 文件 | 36.208 ms | 35.172 ms | 28.798 ms | −2.9% | +22.1% |

两组都是六对中五对更快。32 文件逐对变化为 −12.66%、−2.08%、−3.09%、+2.04%、−9.81%、−0.84%；
波动明显，只报告小幅改善。Redis 执行命令中位数仍为 4,096 / 4,256（256 轮），热态对象读取均为零。
32 文件的阶段均值减少：open 1.113 ms、fstat 0.096 ms、close 0.110 ms、pread 0.226 ms。
阶段均值与总耗时中位数不混加，也不能由该组合实验单独分出两项代码各自的收益。

混合读写尚未通过：新建 16 MiB 文件的 fio randrw 在原版和候选都出现 EIO。
原始失败保留，不宣称通过混合读写验收。后续目录合并与 1 万文件工作见
[第二轮](../overlay-scale-a355-20261114/README.zh.md)。

[第一轮汇总](evidence/phase1-summary.json) · [第一轮原始记录与检查](evidence/phase1-results.tar.gz) ·
[旧二进制的两批对照](evidence/baseline-results.tar.gz)。
两个压缩包 SHA256 分别为
`f9b324fae8faf2cbc8fada92f004aca70fe07c80921a87d9943b63173670cf80`、
`ad4d09e37ad15ae0934bc658edcdb0328a44814fff233bd31921d16c9731e072`。

## 复现

依赖：Linux FUSE、Docker、Python 3 + boto3、Rust 工具链；混合读写另需 /usr/bin/fio。
服务由运行器使用固定镜像在本机启动并回收。RustFS 使用
`Ivanbeethoven/rustfs:feat/dynamic-block-size-rebased`，本批固定
`f4ed202508f2153afb7956bae6f7799b60eccd00`，启动前核验镜像来源。

```bash
# 候选提交须已在 /src/brewfs；输出目录必须尚不存在。
bash experiments/overlay-open-a355-20261113/build.sh /src/brewfs /out/build CANDIDATE_COMMIT
bash experiments/overlay-open-a355-20261113/run-compare.sh \
  /out/build/baseline-artifacts /out/build/candidate-artifacts /out/compare
bash experiments/overlay-open-a355-20261113/run-regression.sh \
  /out/build/baseline-artifacts /out/build/candidate-artifacts /out/regression
```

比较脚本使用同一 producer、输入、缓存预算、FUSE 参数，按六种顺序排列三个版本，
每组冷热各六次。记录总耗时、open/fstat/pread/close、目录操作、上层查询、缓存命中、
Redis commandstats 和对象读请求/字节。commandstats 含 Lua 内部命令，不等于网络往返数。
热态要求对象读取为零，同时保留 Redis 计数。

回归脚本另做六对混合读写，分批顺序 A(3次)/B(3次)/B(3次)/A(3次)：4 KiB、70% 读、16 MiB、direct I/O、
每单元 5 秒，最终 fsync；另核验上层改写、截断、删除、创建、重命名、8 次 chmod 和重挂读回。
这是小规模回归检查，不替代完整 fio/xfstests 性能验收。

批次只在同机没有其他存储测量时运行。flock 仅协调本诊断，不能代替检查其他任务。
输出保留逐轮原始值、配置、命令、源码补丁、二进制 SHA、Redis/对象计数及清理结果。

## 正确性检查

先证明旧实现不能通过新增测试（两项失败），候选四项全部通过，覆盖快照共享和 chmod 失效、立即完成、
调用者取消后继续执行及排空等待、panic 后拒绝继续工作。真实 Redis 权限查询测试通过。
默认 CI 的构建、检查、严格 Clippy、1,362 项主库测试及工具测试通过，266 项 ignored。
实测 feature 组合 2,853 项通过、489 项 ignored、1 项既有 SQLite 并发 atime 失败。
旧版本已复现该失败，按此前用户指示不阻断 Redis 测量；不声称完整 feature CI 全绿。
七项 shell 检查首次因 Windows CRLF 失败，远端副本转 LF 后全部通过，原日志保留。
