# C2：Packed 和 entity-native 的下一步

**当前两条路线都不启动。** 冻结的 BrewFS `ca07159f3da4b670e4aeb878faef928e69bc1634` 在 Entity FUSE 路径缺少这条 trace 成功执行过的文件操作。换 Packed 或 native lower 都走同一个 `EntityFuse`。已验收的 flat BrewFS 是另一条路线，`native-workspace`（`workspace-native-v2`）也不走 Entity FUSE，需独立资格验证。见[核对记录](evidence/preparation/c2-entity-feasibility-01/audit.json)。

| 操作 | 成功回调数 | 首次成功序号 | 当前 Entity FUSE |
|---|---:|---:|---|
| `statfs` | 7 | 35 | 无实现；默认返回 `ENOSYS`，真挂载确认系统调用结果 |
| `chown` | 3 | 205 | `setattr` 拒绝 UID/GID |
| `utimens` | 4 | 1,950 | `setattr` 拒绝时间字段 |
| `rename` | 184 | 5,776 | 有 namespace 原语，FUSE 未接 |
| `symlink` | 8 | 6,682 | 有 `readlink`，无 FUSE 创建入口；远端读回工具也不验符号链接 |
| `link` | 10 | 1,025,265 | 有 namespace 原语，FUSE 未接 |

初始树 100 个目录、614 个文件，35,526,497 字节。初始没有符号链接或硬链接组，文件和目录都有各自权限，源 UID/GID 为 1000:1000。最终树有 5,246 项、205,998,305 文件字节、7 个符号链接；最终硬链接组为空，但中途确实创建过硬链接，不能跳过。保持原始初始树、blob、回调和最终树；不要靠复制链接、改权限或忽略成功回调来通过。

## 产品补齐后按以下顺序执行

1. **小挂载先验。** 核对最新分支、冻结源码和二进制摘要。现有空闲执行节点上用几个文件逐项验证表中的系统调用，再核 `TCGETS` 和 `getxattr`。严格模式唯一允许的拒绝码差异为 `TCGETS: ENOTTY → EOPNOTSUPP`；保留每次差异，原 trace 有 1,048 次探针。其他 errno 和中间读内容照常比较。小检查失败就停止，无需开新机或完整回放。
2. **一次发布，完整核父树。** 复用 C3 [Packed 发布器及参数](scripts/prepare-packed.py)，单独增加 C2 入口：输入改为原 `pytest-full-02/initial`，以锁定的 `initial-tree.json`、源硬链接组和 UID/GID 代替 C3 生成器，使用 `visible-links`、4 MiB chunk/block；父卷、对象前缀和 Redis 绑定均记录。逐项核路径、类型、权限、字节 SHA256、硬链接拓扑，记录父树的 UID/GID；拒绝默认 chmod/chown 掩盖导入问题。只创建一次新资产，保留实际发布的 manifest、原始输出和正常退出回执。
3. **一个新 child 做完整严格回放。** 从该父树创建独立 volume/workspace/namespace；复用 [`qualify-trace.py`](scripts/qualify-trace.py) 的 `--prepared-root --portable-terminal-probe --cache-parent-fds`，锁定原 manifest 和已验收回放器。核 1,747,109 次回调、281,774,624 B 读、194,706,661 B 写、无遗留句柄。核最终树、硬链接拓扑和 SQLite 副本 `quick_check`，历史为 1 个会话、26 条消息、109 个 part；SQLite 检查不写被测树。采集独立阶段，记录两次间隔至少一秒的零上传债务，正常卸载并核原进程退出、挂载消失。
4. **读回刚写的同一 child。** 复用 C3 [只读核验](scripts/packed-c3-node.py)和 [Redis/S3 读取器](scripts/verify-entity-remote.rs)，将小文件生成器替换为锁定的 C2 初始/最终清单与硬链接清单。读取器增加符号链接原始目标和权限对照，并允许对 SQLite 对应路径保留经 SHA 核验的本地副本；核 SQLite 历史可沿用上一步。独立进程、空本地缓存、限定 Redis 前缀和只读对象访问，读实际 manifest、Redis 行和 S3 对象，核原 child 和原父树、authority、对象/切片元数据不变，保留写入拒绝和前后摘要。Entity 挂载仅支持新身份；不得新建 child、重挂失败后复制数据，或读取工作缓存来代替这个证明。
5. **最后做观测校准和正式计时。** 每次新 child、相同输入和目录句柄策略，无模型、无等待；严格回放与冷校验不计入测量，性能模式仍重放全部回调，最终读回独立检查。先按计划做三对采集开关校准（wall 和 P99 不超过 5%）；通过后才计正式回放 wall、回调/字节/失败数、客户端 CPU/RSS、元数据 RPC、S3 GET/Range/PUT 请求及字节、排空时间。正式结果要等原始指标归档及同一 child 校验通过。C2 衡量回放耗时，不代表整段 Agent 任务或会话迁移耗时。

`entity-native` 复用步骤 1、3、4、5；步骤 2 用一次 `--entity-native-source` 取得实际 manifest，后续用 `--entity-native-manifest-key`，读回使用读取器的 `native` 入口，payload cache 固定 0。按当前代码，此输入最多每目录 151 项，4 MiB 下约 1,415 个 native lower 行，均低于对应限制 4,096 和 32,768；C3/C4 的 10 万文件上限不适用于这份 C2 输入。Packed 默认配置与它的比较含布局和缓存差异；若要单独回答格式路径收益，用双方缓存均为 0 的已定义对照，单列结果。
