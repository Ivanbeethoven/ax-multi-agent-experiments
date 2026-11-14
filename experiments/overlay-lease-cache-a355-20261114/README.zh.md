# Private Workspace 缓存与目录热读

[结果与限制](RESULTS.zh.md) · [三对逐次数据](evidence/comparison.md) · [原始归档](evidence/results.tar.gz)

只使用已有 A355；元数据为独立 Redis，对象服务为独立 RustFS。无 SQLite，无新云资源。同机配对顺序执行，测试持有 `/run/lock/ax-overlay-open-diagnostic.lock`。

本轮比较上一轮 `0844a9d` 与本轮代码。默认缓存由 60 秒到期改为依赖有效租约和已有写入失效机制；仍限制在 64 MiB。目录扫描保留远端租约检查，版本不变时复用已建立的目录视图。

| 测试 | 输入与重复 | 记录 |
|---|---|---|
| 热读 | 2、32、10,000 个实际文件；各 3 对，交换顺序 | 每次 open、close、pread、目录操作；Redis 命令；对象请求和字节；缓存计数 |
| lower 对照 | 相同数据和新版二进制的只读 lower | 与新旧 Overlay 分开报告 |
| 61 秒消融 | 同一个新版二进制，分别使用 TTL 与 lease 策略；32 文件预热后空闲 61 秒，各 3 次 | 首轮权限/区间回填、阶段耗时和实际 Redis 请求 |
| 正确性 | 原子替换、混合修改、chmod、重新挂载 | 每次内容校验、最终 fsync 与读回 |
| 混合 I/O | 16 MiB 已写入并读回的文件，4 KiB direct，70% 读，30 秒；3 对 | 吞吐、读写 P99、含最终 fsync 的耗时 |

构建、测试和运行入口：

```bash
# 在源码所在机器打包；新增源码文件先 git add，源码需已通过 rustfmt。
python3 stage_source.py --source /path/to/brewfs --out /new-stage
# 将 /new-stage 原样传到 A355 的全新目录，以下在 A355 执行。
run=/srv/overlay-lease-cache-rerun
tar -xzf "$run/source.tar.gz" -C "$run"
bash build_check.sh "$run"
# 完成构建与测试后；参数为二进制目录、二进制目录、新结果目录。
bash run.sh /baseline-artifacts "$run/artifacts" "$run/results"
python3 summarize.py "$run/results"
```

`build_check.sh` 使用 A355 已有工具链与 Cargo 缓存。`run.sh` 调用仓库已有挂载、数据生成、计数及回收脚本。运行时会核对 RustFS 指定分支和镜像来源；网络受限时通过已有 SSH 转发访问本机代理，不创建服务器。

两版输入、FUSE 参数和缓存容量一致，使用正常优化的 release 构建。每单元重新挂载并预热，以原生对象计数证明计时段 lower 已热。正常性能批次关闭分段诊断；`profile_aged.py` 的 HTTP 采样和 61 秒等待不混入正常热读结论。

耗时开销统一为 `Overlay / 对照 - 1`。分别列新/旧和新/lower，保留三次结果；有波动就报告波动。零对象读取不代表没有 Redis 查询。

`BREWFS_WORKSPACE_ROW_CACHE_POLICY=ttl` 可恢复旧的到期策略；默认 `lease`。两者保留相同写入校验。目录的 OPENDIR 与首次 READDIR 仍各做远端租约检查，因为提前撤销没有即时通知。

`archive.py` 保存逐次数据、实际命令、源码清单、补丁、二进制 SHA、校验日志和失败记录；排除二进制、构建目录及原始工作区副本。
