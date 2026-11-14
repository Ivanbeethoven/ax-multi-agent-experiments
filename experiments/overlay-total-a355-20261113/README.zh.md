# A355：完整 Overlay 读取路径对比直接 Packed 读取

> **2026-11-13 更正：下表不能作为内核缓存相同条件下的 Overlay 开销。**
> 后续拆分发现：虽然两边 JSON 都写了 TTL=0，独立 Packed 实际返回 1 秒，Workspace 返回 0；
> 原始 `after.stats` 中 `brewfs_fuse_attr_valid_ns` / `brewfs_fuse_entry_valid_ns` 已能证明这一点。
> 原因是只读路径读取 `BREWFS_CACHE_TTL_MS`，默认 1000 ms，并未采用这项挂载配置。
> 下方历史数字、原始包和回执保留。实际 TTL 对齐后的测量与代码优化见
> [打开关闭路径诊断](../overlay-open-a355-20261113/README.zh.md)。修正对照条件不算代码提速。

目标：读取同一个不可变 Packed 快照，测量增加完整 Workspace Overlay 后的耗时。
本轮使用现有 A355，Redis/RustFS 在本机回环地址运行；没有新建云资源。

## 结果

48 个计时单元和 24 个计数校验单元全部通过。下表取六组中位数；热读为连续 256 轮的平均每轮时间。
百分比表示**耗时增加**，不是吞吐下降比例。

| 输入 | 状态 | 直接 Packed | 完整 Overlay | 额外耗时 | 六对增加比例范围 |
|---|---|---:|---:|---:|---:|
| 2 文件 | 冷首轮 | 21.37 ms | 33.55 ms | +57.02%（12.18 ms） | +35.34%～+74.07% |
| 2 文件 | 热读每轮 | 1.38 ms | 3.96 ms | +187.83%（2.59 ms） | +182.54%～+203.80% |
| 32 文件 | 冷首轮 | 41.50 ms | 111.83 ms | +169.45%（70.33 ms） | +153.99%～+180.93% |
| 32 文件 | 热读每轮 | 14.70 ms | 36.67 ms | +149.40%（21.97 ms） | +146.05%～+169.16% |

在这组小文件读取负载下，热态完整 Overlay 路径是直接 Packed 的 **2.50～2.88 倍耗时**。
旧实验约 2% 只比较空上层解析，两边共同保留了 Workspace 校验、权限和读计划等路径，不能代替本表。

将挂载就绪也计入冷启动：2 文件为 **49.22 → 76.73 ms（+55.88%）**，
32 文件为 **69.93 → 154.53 ms（+120.97%）**；这里仍排除采样等待和工作区创建时间。

| 核对项 | 直接 Packed | 完整 Overlay |
|---|---:|---:|
| 2 文件冷首轮 HTTP 请求 / 响应体 | 11 / 27,412 B | 11 / 27,412 B |
| 32 文件冷首轮 HTTP 请求 / 响应体 | 11 / 166,844 B | 11 / 166,844 B |
| 热首轮、后续循环的对象读取 | 全部为零 | 全部为零 |
| 2 文件连续 256 轮 Redis 命令中位数 | 0 | 4,096 |
| 32 文件连续 256 轮 Redis 命令中位数 | 0 | 4,256 |

Redis 计数扣除了 INFO，包含 Lua 内部调用，不能直接当作网络往返数。
挂载阶段直接 Packed 比 Overlay 多一次 64 B manifest 探测，已经单列在原始统计中。
冷首轮对象读取相同、热读对象读取为零，说明本表差异不是 lower 未预热造成的。

热态单次 pread 中位数：2 文件 **178.97 → 207.72 µs**，32 文件 **180.47 → 206.51 µs**；
打开并列目录为 **456.86 → 1699.50 µs**、**670.21 → 2628.25 µs**。
整轮还包括文件打开、fstat、关闭和内容校验；本轮未做 Rust 函数级耗时归因。

[计时汇总](evidence/measurement-summary-01.json) · [计数校验](evidence/validation-summary-02.json) ·
[原始数据与冻结脚本](evidence/overlay-total-results.tar.gz) · [归档回执](evidence/archive-receipt.json) ·
[本地核验](evidence/local-verification.json)。结果包 7,399,589 B，已逐文件核验 674 + 338 个文件。

## 两条实际路径

| 条件 | 挂载入口 | 元数据与读计划 |
|---|---|---|
| `production` | `workspace` + Packed lower | 正常可写 `WorkspaceMetaLayer`；保留租约、权限、上层检查及读计划合并，upper 为空 |
| `packed-direct` | `packed-snapshot` | 独立 `PackedReadonlyMeta`；直接查询同一快照，没有 workspace ID、上层或 Redis 元数据 |

对照使用主线已有的 `mount_packed_readonly_with_client`，不是旧实验的 `lower-read` 开关。
两边共享 VFS/FUSE 和 Packed 解码器。虽然通用读计划接口名为 `WorkspaceReadPlanProvider`，
对照的实现直接调用 `AuthenticatedSnapshot::prepare_inode_read_observed`，不构造 `WorkspaceMetaLayer`，
也不调用 `prepare_overlay_plan`。脚本要求对照在挂载、预热和计时期间的 Redis 命令增量均为零（扣除采样 INFO），
Workspace 操作计数为零，并验证写打开返回 EROFS。

BrewFS 基于 `03c8cc55d61473533c00a8afe13b4ef1fcd9b725`，冻结时仍为 main。
[唯一源码补丁](retention-parity.patch)让独立 Packed 挂载可以显式使用现有的帧缓存；
没有改动 Workspace 生产路径，没有应用旧的上层绕过补丁。独立挂载的默认帧缓存仍为零。

## 相同的数据和缓存

- 每个输入只生成一次快照，两条路径读取相同的 manifest 和对象，逐文件核对 SHA256。
- 输入为 2 文件（13 B + 64 B）和 32 文件（前两文件加 30 × 4 KiB，共 122,957 B）。
- 同一个 `opt-level=3` 二进制；chunk 64 MiB、block 4 MiB；数据和元数据 codec 为 raw，inline 关闭。
- Packed 元数据缓存 256 MiB、保留帧缓存 8 MiB；运行时核对 `.stats` 中的实际容量。
- 两边 VFS 块缓存、SSD 缓存和整包磁盘缓存均为零，预取关闭。热读由同一实现的 Packed 帧/元数据缓存提供。
- FUSE 4 workers、max_background=128、TTL=0、keep_cache=false、direct I/O 开启；逐轮核对 FUSE 读取次数。
- Overlay 自己的元数据行缓存为 64 MiB、TTL=60 s；直接 Packed 路径没有上层元数据。
- Redis/RustFS 镜像、服务资源限额、后端和宿主页缓存状态保持一致。

## 执行与计时

两个输入 × 两条路径 × 冷/热两种状态 × 六组，共 48 个测量单元。
每个单元先准备并核对文件，正常卸载，再用新进程和空缓存目录重挂；每组交换路径顺序，三组 AB、三组 BA。

冷组省略预热，第一轮必须产生实际对象读取；热组先读五轮，计时首轮必须零对象读取。
第一轮单列，其后连续 256 轮单列；后续循环均要求对象读取为零。
每轮依次列目录、查询属性、逐文件打开/读取/核对内容/关闭。

报告三个时间：挂载就绪、首轮、连续热读每轮；另算“挂载 + 首轮”，避免把挂载期间的工作藏起来。
挂载就绪以 1 ms 间隔轮询。计数采集和 300 ms 刷新等待位于计时区间外。
这里的冷态是客户端冷态，后端与宿主页缓存不清空。工作区创建和真实 fork 不在计时范围。

先用 24 个短单元交叉核对独立代理与 BrewFS 请求/响应体计数，随后直连 RustFS 完成 48 个计时单元。
Packed 请求数包括 HEAD；下载字节包括帧及元数据编码，不等于应用读取字节。

性能增加比例 = `(Overlay 时间 / 直接 Packed 时间 − 1) × 100%`。
同时保留每对差值、六次原始值、pread/列目录延迟、Redis 命令、对象读取和缓存计数。

## 复现

需要 Linux、FUSE、Docker、Python+boto3、Rust 1.99，以及运行器中固定 digest 对应的镜像。
RustFS 使用 `Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased`，开始前核对分支和镜像源码标签。

```bash
bash build.sh /absolute/brewfs-clone /absolute/new-build
sudo python3 ../hot-lower-20261112/run_local.py \
  --binary /absolute/new-build/artifacts/brewfs \
  --producer /absolute/new-build/artifacts/packed_snapshot_fixture \
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 \
  --source-patch /absolute/new-build/artifacts/source.patch \
  --workspace-format workspace --build-profile optimized-release \
  --comparison overlay-total --object-meter client \
  --cache-state on --metadata-sync remote \
  --pairs 6 --passes 256 --out /absolute/new-run
python3 summarize.py /absolute/new-run > /absolute/new-summary.json
```

计数校验用 `--object-meter proxy --pairs 3 --passes 1`，输出到另一个新目录；其时间不用于性能比较。
结果保存逐次样本、分阶段计数、配置、源码和二进制 SHA、实际命令、清理回执与文件清单。

仅重新核验和汇总已交付数据，不启动服务：

```bash
python3 verify_evidence.py --out /absolute/new-verification-directory
```

## 版本、检查与收尾

- BrewFS 二进制 SHA256：`a797321fa0df7a002e2ad6350620e5f4f5397f9ca16bd3cae86812d93e42aee6`。
  同一产物用于所有条件；构建采用 Git archive，准确版本由基线提交、补丁和 SHA 共同标识。
- 补丁 SHA256：`647970c1af06eeafd125dd0af608c21561b6e3644513437198164b836ab36484`。
  对归档源码逐文件核对，除换行外只有 `src/cli.rs` 改动。
- 测量脚本 SHA256：`9e0d80efd341c776dc85c608e132050365ffc6755d7bc16dcbe31ffec01b94f6`。
- RustFS 指定分支提交 `f4ed202508f2153afb7956bae6f7799b60eccd00`；各批核对源码标签与镜像 digest。
- Rustfmt、严格 Clippy、5 项 Python 测试通过。测量 feature 下的 Rust workspace lib/bin 测试
  2849 通过、1 失败、489 ignored；唯一失败仍为此前未改主线也复现的 SQLite atime 测试。
  用户已允许继续 Redis 测量；[检查回执](evidence/scope-check.json)明确不宣称完整 CI 通过。
- `validation-01` 在 GitHub 分支查询阶段超时，尚未开始测量；原记录保留。
  接通仅供版本核对的代理后，`validation-02` 和 `measurement-01` 完成。
- 本轮私有 Redis/RustFS 容器、FUSE 挂载和临时数据已清理，版本核对隧道已关闭；其他任务资源未改动。
