# A355：BrewFS Overlay 冷读、热读与 native Workspace

**完整 Overlay 读路径的后续对照见 [直接 Packed 对比结果](../overlay-total-a355-20261113/README.zh.md)。**
本页保留空上层解析和 native/workspace 比较的原始结果。

本轮使用用户已有的 A355（`idcai-client2`），Redis 与 RustFS 只在本机回环地址运行。
未使用其他 C2 节点，没有新建服务器。
已完成 36 项热读对照、[96 项冷/热比较](#冷读与-native-overlay-结果)和 48 项计数交叉校验。

## 已完成的热读结果

36 个单元通过内容、热态、对照计数与清理检查。每个输入 6 组，每次 1024 轮。
下表是各条件总耗时的中位数，差值按两个中位数之比计算。

| 输入 | 跳过空上层解析 | 保留上层解析 | 差值 | 逐对差值范围 |
|---|---:|---:|---:|---:|
| 2 文件，77 B | 4.349 s | 4.439 s | +2.06% | −3.50%～+6.13% |
| 32 文件，122,957 B | 38.868 s | 39.649 s | +2.01% | −1.64%～+7.31% |

这组比较只衡量**缓存命中后，空 upper 的解析成本**。逐对波动跨过零，不能把约 2%
写成稳定、通用的 Overlay 总开销。正常可写路径另测得 4.396 s / 40.096 s，
比跳过空上层的对照高 1.08% / 3.16%；该差值同样包含运行波动及只读控制自身的影响。

状态对应 fork 后尚未修改文件、lower 已热的子工作区。实际脚本为新建工作区绑定同一
Packed 快照，**没有计时真实 fork，也没有运行跨机迁移**。

| 计数或延迟（单次测量中位数） | 2 文件保留 / 跳过解析 | 32 文件保留 / 跳过解析 |
|---|---:|---:|
| 单次 pread 中位数 | 230.04 / 226.48 µs | 239.34 / 233.33 µs |
| 打开并列目录中位数 | 1893.08 / 1887.81 µs | 2539.33 / 2464.56 µs |
| 上层区间后端查询 | 0 / 0 | 0 / 0 |
| 上层区间空结果缓存命中 | 2048 / 0 | 32768 / 0 |
| 上层目录后端查询 | 0 / 0 | 0 / 0 |
| 上层目录空结果缓存命中 | 2048 / 0 | 2048 / 0 |
| Redis 命令（扣除 INFO，含 Lua 内部调用） | 16384 / 16384 | 17173 / 17013 |
| RustFS 请求、下载字节 | 0、0 / 0、0 | 0、0 / 0、0 |

36 个单元的上层区间/目录后端查询及 RustFS 请求、字节均为零，不仅是中位数为零。
Redis 的其他调用仍存在；32 文件每个条件还记录到约 20 次 `zrangebylex`，不能据上层
查询计数为零就声称 Redis 范围查询全部消失。本轮未逐条归因后台维护命令。

完整数据见 [汇总](evidence/summary-02.json)、[归档回执](evidence/archive-receipt.json)
和 [原始结果包](evidence/a355-results.tar.gz)。结果包约 12.85 MB，本地已核验 326 个原始文件。

## 对照

基于 BrewFS `03c8cc55d61473533c00a8afe13b4ef1fcd9b725`，另加只用于本次测量的补丁。
主线没有 lower-only 开关；补丁适配了旧实验的空 upper 对照，没有覆盖旧优化补丁或修改主线。
三个条件共用同一份 `opt-level=3` release 二进制：

| 条件 | 操作 |
|---|---|
| `production` | 不设置实验开关，正常可写 Workspace 的读取路径 |
| `overlay-read` | 验证 upper 为空、禁止修改，保留上层区间、目录项和属性解析 |
| `lower-read` | 同样验证和禁止修改，跳过已证实为空的上层解析 |

两种只读对照保留同样的权限、租约、lower、FUSE 和读计划路径。对照差值衡量的是
**空 upper 的解析开销**，不包括两边共同保留的读计划和权限等成本。
正常生产路径另外记录，用来识别实验开关自身的影响。

代码核对还确认：两臂都会执行 `begin_request` 的 inode/fence 捕获、`prepare` 中的
上层读计划预算申请、inode 解析、`prepare_overlay_plan`，以及 Workspace 的目录包装。
因此 `lower-read` 不是直接挂载 `packed-snapshot`。若测完整 Overlay 成本，需要另立
真正的只读 Packed 基线，并对齐两条路径的缓存与生命周期要求。

## 输入和计时

- 原两文件：13 B 和 64 B。
- 32 文件：上述两文件加 30 个不同内容的 4 KiB 文件，共 122,957 B。
- 每种输入做 6 组，覆盖三个条件的全部 6 种执行顺序；每次测 1024 轮。
- 每轮打开并列出 `sub`，查询属性，然后逐文件打开、读取、核对字节、关闭。总时间包含这些操作及 Python 校验。
- 元数据缓存为 64 MiB、TTL 为 60 秒；lower 帧保留 8 MiB，VFS 读缓存为 512 MiB。
  FUSE 使用 4 个 worker、TTL=0、keep-cache 关闭、直接读开启。
  `BREWFS_PACKED_VERIFY_MEMO=0` 在三臂相同；帧保留缓存仍开启。
- 每次先预热；最后一轮预热和计时阶段都要求 RustFS 请求、收发字节、未完成请求增量为零。
  Redis 命令单独采集，包含 Lua 内部调用，不能直接当作网络往返次数。
- 计时前完成编译和检查；正常生产路径和两个对照都逐次核对内容与目录。
  两个受控模式在计时后验证写打开被拒绝，随后正常卸载。

## 复现入口

使用 Rust 1.99.0、已有 BrewFS 克隆及所需 Linux 构建依赖，可以在独立 worktree 构建：

```bash
bash build.sh /absolute/brewfs-clone /absolute/new-build
```

已有固定镜像、FUSE、带 boto3 的 Python，以及由本目录补丁构建的 release 程序后：

```bash
sudo python3 ../hot-lower-20261112/run_local.py \
  --binary /absolute/new-build/artifacts/brewfs \
  --producer /absolute/new-build/artifacts/packed_snapshot_fixture \
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 \
  --source-patch /absolute/new-build/artifacts/source.patch \
  --workspace-format workspace --build-profile optimized-release \
  --comparison overlay-tax --cache-state on --metadata-sync remote \
  --pairs 6 --passes 1024 --out /absolute/new-run
python3 summarize.py /absolute/new-run > /absolute/new-summary.json
```

输出包含逐次时间、Redis 命令、RustFS 请求和字节、缓存及对照计数、配置、二进制和补丁 SHA。
汇总程序先检查文件校验和、执行顺序、输入一致性、热态和清理，再计算各条件总时间中位数、逐对差值与范围。

只重新计算已交付数据：

```bash
mkdir /tmp/a355-result
tar -xzf evidence/a355-results.tar.gz -C /tmp/a355-result
python3 summarize.py /tmp/a355-result/run-02
```

## 冷读与 native-overlay 结果

`cold-native-04` 已完成 **96 个单元**：两个输入 × 四个条件 × 冷/热两种状态 × 六组。
每组打乱条件顺序。下表为六次中位数；“连续热读”取预热组随后 64 轮的平均每轮时间。
一轮包含列目录、属性查询、逐文件打开/读取/关闭和内容校验。

| 输入 | 路径 | 冷首轮 | 预热后首轮 | 连续热读 / 轮 | 冷首轮对象下载 |
|---|---|---:|---:|---:|---:|
| 2 文件，77 B | Packed Overlay，正常可写 | 41.74 ms | 6.06 ms | 4.51 ms | 35,873 B |
| 2 文件，77 B | native/workspace Overlay | 19.82 ms | 6.91 ms | 4.35 ms | 85 B |
| 32 文件，122,957 B | Packed Overlay，正常可写 | 126.76 ms | 43.82 ms | 38.78 ms | 172,153 B |
| 32 文件，122,957 B | native/workspace Overlay | 176.12 ms | 37.59 ms | 35.13 ms | 123,085 B |

native-overlay 使用当前 `workspace init-volume`，文件通过 FUSE 写入可写层并 fsync；
Packed 组新建工作区绑定共享快照。两者分别体现普通 Workspace 和 Packed lower 的读取路径。
数据布局不同，表中差值包含布局及缓存路径的影响。真实 native 非空父层 fork、跨机和大负载尚未测量。

冷组先准备并核对文件，正常卸载，再用新进程和新的空客户端缓存目录重挂，省略全部预热。
冷首轮都测到实际对象读取；48 个预热单元的首轮以及全部 96 个单元的后续循环，对象读取均为零。
这里清空的是客户端缓存；Redis、RustFS 和宿主页缓存保持原状。挂载和预热时间单列，
阶段间等待计数刷新不计入读耗时。挂载就绪每 100 ms 检查一次，记录的 `mount_ms` 不用于细粒度比较。

### 冷首轮的上层解析成本

| 输入 | 保留 / 跳过空上层解析 | 时间差 | Redis 命令：保留 / 跳过 | 上层区间、目录后端查询：保留 / 跳过 |
|---|---:|---:|---:|---:|
| 2 文件 | 41.99 / 40.46 ms | +1.53 ms（+3.77%） | 33 / 21 | 2、2 / 0、0 |
| 32 文件 | 127.77 / 107.91 ms | +19.86 ms（+18.40%） | 183 / 81 | 32、2 / 0、0 |

两臂的对象请求与字节相同。冷启动尚未缓存空 upper 的结果，首次查询成本因此重新出现。
32 文件六对差值为 −6.35%～+36.70%；上述百分比是中位数之比。热态的空 upper 成本见前表。
Redis 命令包含 Lua 内部调用；它不是网络往返计数。

### 对象计数与测量校准

最终计时直连 RustFS。Packed 读取使用 `layer="http_attempt",engine="packed"` 的
`brewfs_object_read_started_total` 和 `brewfs_object_read_received_body_bytes_total`；
native 使用 `brewfs_s3_get_ops_total` / `brewfs_s3_get_bytes_total`。
Packed 冷首轮每次 16 个 HTTP 请求（含 HEAD）；native 分别为 2 / 32 个 GET，
两者请求口径不同。字节均为收到的响应体，包含编码开销或 Packed 元数据，并非应用读取量。

`cold-native-03` 的 48 个单元用独立代理校验，客户端请求/字节数与代理逐单元一致。
native 计数每 100 ms 汇入 `.stats`，因此阶段结束后在计时区间外等待 300 ms 再采集。
最终 `cold-native-04` 的代理字段未使用，实际请求量取 `first_objects` / `steady_objects`。

早期代理会为 4 KiB GET 引入约 42 ms 延迟；关闭 Nagle 后约 1.48 ms，直连约 0.27 ms。
因此 `cold-native-02` 只保留功能和计数证据，其耗时不进入结果表。
原热读批次计时阶段没有对象请求，未经过该延迟路径。

### 复现冷/热比较

在本目录执行，参数中的构建目录与上面的 `build.sh` 输出对应：

```bash
sudo python3 ../hot-lower-20261112/run_local.py \
  --binary /absolute/new-build/artifacts/brewfs \
  --producer /absolute/new-build/artifacts/packed_snapshot_fixture \
  --source-commit 03c8cc55d61473533c00a8afe13b4ef1fcd9b725 \
  --source-patch /absolute/new-build/artifacts/source.patch \
  --workspace-format workspace --build-profile optimized-release \
  --comparison cold-native --object-meter client \
  --cache-state on --metadata-sync remote \
  --pairs 6 --passes 64 --out /absolute/new-cold-run
python3 summarize_cold.py /absolute/new-cold-run > /absolute/new-cold-summary.json
```

先做计数校验时将 `--object-meter` 改为 `proxy`、`--pairs 3 --passes 1`，使用另一个输出目录。
运行器需要 root、FUSE、Docker、boto3、两个已固定镜像及可访问 GitHub 的分支核对；
镜像和当前分支不匹配时会停止。历史复现应使用归档内的冻结脚本和产物版本。

[最终汇总](evidence/summary-cold-native-04.json) · [96 单元原始结果](evidence/cold-native-04-results.tar.gz) ·
[计数校验汇总](evidence/summary-cold-native-03.json) · [48 单元原始结果](evidence/cold-native-03-results.tar.gz) ·
[归档 SHA 与清理回执](evidence/cold-archive-receipt.json)。归档内含原始逐次数据、配置、版本、命令、冻结脚本和清单；
本地已核验最终批次 1,250 个文件、计数校验批次 626 个文件。

重新核验下载的证据（仅解包和计算，不启动服务）：

```bash
python3 verify_evidence.py --out /absolute/new-verification-directory
```

## 产物与检查

- BrewFS 基线：`03c8cc55d61473533c00a8afe13b4ef1fcd9b725`；[实验补丁](ablation.patch)
  SHA256：`7cda6c9524b2440bc61a3ad9057814100088e2b735ce4d733b7616201cbdb466`。
- 同一 `opt-level=3` release 二进制 SHA256：`4c354ad2abc27fe4b9da509e92a246d0cc120183ee45f6046d61826d6ff115d0`。
  构建源来自 Git archive，二进制内嵌 Git 标识为 unknown；准确版本由提交、补丁和 SHA 共同记录。
- RustFS 指定分支 `feat/dynamic-block-size-rebased` 为 `f4ed202508f2153afb7956bae6f7799b60eccd00`。
  镜像 digest 与源码标签在每批开始时重新核对。
- Rust fmt、严格 Clippy、新增 3 项对照测试通过；完整库测试 2851 通过、1 失败、489 ignored。
  唯一失败是 SQLite 多实例 atime 测试，未改主线程序在 A355 也连续 5 次失败；原日志全部保留。
  用户已明确这项不阻塞 Redis 测量，并要求之后移除 SQLite 测试；本轮没有删除上游测试。
- AX 仓库检查、归档检查通过；冷读扩展前的单元测试 704 项、26 skip。
  冷读扩展另通过 4 项 HTTP/计数测试、48 项计数交叉校验和 96 项直连集成测量。
  抽取挂载函数时发现旧的 metadata-remount 分支仍引用原局部变量，已修正；
  [6 项实际修改及重挂回归](evidence/remount-regression-receipt.json)全部通过。
  最终计时脚本 SHA 为 `ae59ad932f77e8e42b748c84ee3f2b1a054180ec9eef482c6d0b60f7ded6ef4f`，
  归档中保留原脚本；仓库脚本仅另修正该未进入计时的 remount 分支。
- `run-01` 在计时前因 GitHub 分支核对超时失败，已保留。`run-02` 为完整热读批次。
- `cold-native-01` 因脚本对嵌套 Redis 计数做减法失败；已修正并补测试。
  [失败与代理校准记录](evidence/cold-failures-and-calibration.tar.gz)、
  [旧代理批次](evidence/cold-native-02-results.tar.gz)保留原样；旧 C2 证据未修改。
