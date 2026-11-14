# Overlay 并发替换与混合读写修复

只使用已获准的 A355；Redis、RustFS 在本机独立容器中运行。没有 SQLite，也不申请云资源。历史结果留在 `overlay-scale-a355-20261114`。

BrewFS 修复提交：[3785e30](https://github.com/brewfs/brewfs/commit/3785e30e9999d5efcab6e46d486a6204460320e2)，工作分支 `codex/a355-overlay-open-cache-20261113`，未合并。

## 结果

两个已复现的错误已修复。优化版通过 600 次原子替换、9,600 次并发读取；日志中 23 次过时 inode 由内核重新解析成功。6 组混合读写共核对 59,129 次读、25,262 次写，经历 11,263 次发布窗口等待，内容、fsync 和重挂检查均通过。另有 6 组 16 MiB fio 随机混合读写通过。旧版的 ENOENT/EIO 失败证据保留。

| 热读输入 | 修复前 | 修复后 | 耗时变化 |
|---|---:|---:|---:|
| 2 文件，257 轮 | 0.933320 s | 0.941798 s | +0.91% |
| 32 文件，257 轮 | 9.050151 s | 9.027474 s | −0.25% |
| 10,000 文件，3 轮 | 42.194417 s | 41.753027 s | −1.05% |

每组为 3 对重复的中位耗时。小输入有正负波动，一万文件三对变化为 −0.40%、−2.09%、−1.05%；本次主要解决正确性，不据此宣称稳定的热读加速。相对同引擎直接 lower，三个规模的耗时开销分别为 41.25%、19.19%、54.78%。

27 个热读单元在计时阶段均为零对象请求、零对象字节。一万文件组新旧版每单元均有 10,000 次上层区间查询、50,641 条 Redis 执行命令；本次没有消除这些元数据访问。该组平均 open 累计耗时从 24.539 s 变为 24.281 s，pread 从 10.520 s 变为 10.364 s，open 仍是主要耗时部分。

混合写回仍有明显收尾成本：64 KiB 校验负载的最终 fsync 为 0.94–2.12 s。fio 的 5 秒 active 阶段读带宽为 0.83–0.92 MiB/s、写带宽为 0.34–0.38 MiB/s，包含最终 fsync 的进程墙钟为 8.00–11.11 s。旧版 fio 失败，因此不计算吞吐提升比例。

[每次测量、阶段耗时和查询计数](RESULTS.zh.md) · [正确性回执](evidence/correctness-summary.json) · [原始结果与命令](evidence/results.tar.gz)。压缩包 27,605,164 B，SHA256 为 `b3e225b7825554b402485e479b6687b1ac458a46d58ad4ecdc52ef0128cea600`；已逐项核对包内 815 个有原始校验清单的文件。包含失败尝试，不含二进制和构建目录。

Rust 的相关 KV、权限、租约、缓存、合并写回和新增回归检查通过，编译、Clippy、运行时 feature 和 shell 检查通过。原 shell 文件带 CRLF，shell 检查在独立 LF 副本中通过，转换记录和原失败日志均保留。没有运行含 SQLite 的完整后端测试套件；本轮 FUSE 使用 remote 元数据同步，未重新测跨机和 local-buffered 模式。

## 检查内容

| 检查 | 输入与操作 | 必须满足 |
|---|---|---|
| 并发替换 | 100 次写入、fsync、原子替换；每次并发打开读取 16 次 | 无 ENOENT/ESTALE；只读到本轮或上一轮的完整值 |
| 混合读写 | 64 KiB 文件，4 KiB 随机读写，70% 读；固定随机种子，至少 8 秒 | 每次读取与内存中的预期值一致；最终 fsync、关闭重开、重挂内容一致 |
| fio | 16 MiB 已写满并核验的文件，4 KiB 随机读写，70% 读，direct=1，5 秒 | 无错误；记录 active BW、延迟、包含最终 fsync 的墙钟时间 |
| 热读对比 | 2、32、10,000 个真实文件，目录分页；每组 3 对，交换顺序 | lower 热态证明；逐次 open/readdir/pread/close、Redis 实际命令数、对象请求及字节数 |

正确性检查沿用原运行器的修改、删除、fsync 和重新挂载验证，每种负载在两种初始文件树上各跑 3 次。失败立即停止，不丢弃失败样本后重跑同一输出目录。

保留到重挂验证的混合读写文件使用 64 KiB，以符合原运行器“一次整文件读取对应一个 FUSE 回调”的计数检查；独立 fio 仍使用 16 MiB。首次 debug 预检的 16 MiB 文件通过逐次内容检查后，被该回调计数检查拦住，原记录保留，不算整项通过。

热读的三条路径分别是：上一轮 Overlay、修复后的 Overlay、同引擎只读 lower。行缓存固定为 64 MiB；2/32 文件的 payload 缓存为 8 MiB，10,000 文件为 64 MiB。元数据同步为 remote，FUSE 属性和目录项 TTL 均为零。小负载 257 轮，大负载 3 轮。不同缓存设置分别报告。

## 复测

每个二进制目录必须有 `brewfs`、相对共同源码基点的 `source.patch`；候选目录另放相同的 `packed_snapshot_fixture`。二进制都用正常优化的 release 编译。共同基点是 `03c8cc55d61473533c00a8afe13b4ef1fcd9b725`，上一轮 Overlay 对应 `97db0eb`；具体 SHA 和补丁以本次证据为准。

```bash
export CANDIDATE_COMMIT=填入已审查的修复提交
bash experiments/overlay-fixes-a355-20261114/build.sh \
  /absolute/brewfs-repository 97db0eb "$CANDIDATE_COMMIT" /absolute/new-build
python3 -m unittest discover -s experiments/overlay-fixes-a355-20261114 -p test_workloads.py -v
bash experiments/overlay-fixes-a355-20261114/run.sh \
  /absolute/baseline-artifacts /absolute/candidate-artifacts /absolute/new-output
```

宿主需有 Python 3、Docker、FUSE、fio、flock 和访问指定 RustFS Git 分支的网络。运行器在启动前核对 RustFS 分支与镜像来源，保存输入、命令、二进制 SHA、逐次数据、Redis commandstats、独立 MONITOR 核验及挂载日志。MONITOR 在计时结束后运行。脚本只清理自己创建的容器与临时目录；宿主性能测试锁覆盖整批。

使用预先准备好的 Redis/RustFS 镜像；RustFS 构建与来源核验见[部署说明](../../infra/rustfs/README.md)。本次实际镜像 digest 和源码提交保存在每批 `run.json`，不使用浮动 `latest` 代替。

耗时开销统一为 `Overlay 耗时 / 对照耗时 - 1`。优化前后 Overlay 与 Overlay/lower 两个对照分开报告。旧版混合读写若失败，只记录失败，不能拿已完成的部分 I/O 计算性能收益。

输入生成器核验：本次和上一批 `measure-02/small/run.json` 实际记录的 producer SHA256 都是 `d2df769ffae39554765cd6bc11beb46fff59a0b58a95c30821300392b2fda18f`。旧交付文档中的 `00ee8997…` 是另一份已构建产物的 SHA，不是该批实际调用的生成器；本次以运行回执为准，旧原始数据不改写。
