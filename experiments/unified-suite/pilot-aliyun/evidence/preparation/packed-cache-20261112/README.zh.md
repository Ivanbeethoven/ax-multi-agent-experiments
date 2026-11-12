**Packed 整包缓存候选 6912：实际 FUSE 开关对照，2026-11-12。**

128 文件和 10,000 文件均完成正反顺序对照，每个规模各有 4 对 off/on。10,000 文件冷读均值由 32.917 秒降至 23.438 秒（−28.80%），含挂载、两遍读取、后台空闲观察及卸载的整轮均值由 63.019 秒降至 43.643 秒（−30.75%）；四对整轮耗时均下降 30.59%–30.86%。本记录归档候选 `6912a796` 的性能结果。后续发现的失败下载条目重试问题由独立修复处理，最终默认开启资格须用修复后的二进制另开一轮验证。

这是阿里云一台专机上 loopback RustFS 的实际 FUSE 测量，覆盖 Packed 实际读取路径；不作为跨机 S3 网络性能或正式 C3/C4 suite 结果。`current/design.json` 的正式执行资格不因本记录改变。

每个规模先运行 off-on-off-on，再运行 on-off-on-off；128 的四对完整验证均有收益后才扩展至 10,000。每个四臂运行共用同一不可变快照、同一二进制和配置预算，每臂使用新的挂载及本地缓存目录。两个顺序运行分别生成快照，其逻辑输入 SHA 相同，物理 manifest/revision 分别保留。RustFS 服务端和宿主页缓存没有清空，因此 cold 表示客户端缓存冷态。先真实 readdir，随后按排序路径逐文件 open、pread 4096 字节并校验 SHA-256；再读取一遍。生成器使用默认 random-small-file profile、raw 元数据/数据、inline 关闭、32 文件/目录。

| 文件数 | 阶段 | off 均值（秒） | on 均值（秒） | 均值耗时变化 | 逐对耗时降低范围 |
|---:|---|---:|---:|---:|---:|
| 128 | 冷 readdir + 读取 | 0.429841 | 0.313570 | −27.05% | 24.20%–28.94% |
| 128 | 第二遍 readdir + 读取 | 0.374961 | 0.250462 | −33.20% | 31.91%–33.90% |
| 128 | 整轮 | 1.481919 | 1.240348 | −16.30% | 15.38%–16.78% |
| 10,000 | 冷 readdir + 读取 | 32.917357 | 23.437982 | −28.80% | 28.31%–29.29% |
| 10,000 | 第二遍 readdir + 读取 | 29.350664 | 19.402006 | −33.90% | 33.59%–34.62% |
| 10,000 | 整轮 | 63.018582 | 43.642714 | −30.75% | 30.59%–30.86% |

收益没有覆盖所有阶段。10,000 文件的冷 readdir 从 1.621 秒增加至 2.020 秒（+24.64%），warm readdir 从 0.714 秒增加至 0.733 秒（+2.57%）；挂载从 166.2 毫秒到 168.6 毫秒，卸载从 75.2 毫秒到 124.8 毫秒。这些成本都保留在整轮计时中。两臂均有约 502.8 毫秒的无活跃 GET 观察窗口，计入整轮，不从 on 中扣除。128 冷 readdir 也由 29.34 毫秒增至 36.11 毫秒。

下表每行的请求和字节数在同规模、同开关的 4 次计时臂中一致。GET 包括 Range GET；Range 是其中的子集。字节仅统计 S3 响应 body，包含元数据，不含 HTTP 头。请求由带 TCP_NODELAY 的独立流式代理计数；每臂重置重复请求集合。

| 文件数 | 阶段 | 开关 | GET / Range / HEAD | S3 body 字节 |
|---:|---|---|---:|---:|
| 128 | 冷读 | off | 148 / 148 / 0 | 16,895,099 |
| 128 | 冷读 | on | 15 / 0 / 15 | 649,755 |
| 128 | 第二遍 | off | 128 / 128 / 0 | 16,777,216 |
| 128 | 第二遍 | on | 0 / 0 / 0 | 0 |
| 128 | 整轮 | off | 277 / 277 / 0 | 33,677,810 |
| 128 | 整轮 | on | 16 / 1 / 15 | 655,250 |
| 10,000 | 冷读 | off | 10,970 / 10,970 / 0 | 1,317,020,299 |
| 10,000 | 冷读 | on | 656 / 0 / 656 | 48,574,587 |
| 10,000 | 第二遍 | off | 10,000 / 10,000 / 0 | 1,309,671,424 |
| 10,000 | 第二遍 | on | 0 / 0 / 0 | 0 |
| 10,000 | 整轮 | off | 20,971 / 20,971 / 0 | 2,626,697,218 |
| 10,000 | 整轮 | on | 657 / 1 / 656 | 48,580,082 |

所有计时臂挂载另有 1 次 Range GET、5,495 字节，已经计入整轮；卸载没有新增 S3 请求。on 增加的 HEAD 已列出。10,000 的重复 GET 由整轮 20,001 次降至 0 次；128 由 257 次降至 0 次。

| 文件数 | 开关 | 冷读放大：冷 S3 字节 / 一遍逻辑读 | 整轮传输/逻辑读：S3 字节 / 两遍逻辑读 | 整轮下载/唯一工作集 |
|---:|---|---:|---:|---:|
| 128 | off | 32.224844 | 32.117662 | 64.235325 |
| 128 | on | 1.239309 | 0.624895 | 1.249790 |
| 10,000 | off | 32.153816 | 32.064175 | 64.128350 |
| 10,000 | on | 1.185903 | 0.593019 | 1.186037 |

一遍逻辑读分别为 524,288 和 40,960,000 字节；两遍分别为 1,048,576 和 81,920,000 字节。整轮传输/逻辑读小于 1 来自第二遍本地复用。off 每次读 4 KiB 时反复读取较大的 Packed 帧，且该需求管道没有保留已解码 payload；这解释了本负载下约 32 倍的传输/逻辑读，而非一般 S3 文件系统的固有放大率。

on 的整个挂载期间，整包缓存边界命中率为 128：`261/(261+15)=94.565%`，10,000：`20314/(20314+656)=96.872%`。off 没有启用该缓存，命中率记为不适用。全部 on 计时臂的 `inflight_hits=0`，命中均为 ready hits。本实验直接证明整包落地后的复用收益，不能据此把收益量化为异步下载隐藏等待。当前 fixture 的 `base/objects/1` 对象在 128 时有 5 个、4,647–138,789 字节；10,000 时有 314 个、28,680–138,790 字节。完整对象清单及每种对象的字节统计保留在原始 JSON / summary 中，这些大小不代表一般 GroupMeta 或 pack 的固定大小。

10,000 的初始库存包含 45 个 index 对象、共 7,355,385 字节，以及 628 个 `base/objects` 对象、共 44,749,839 字节；库存总字节与本轮实际下载的 48,574,587 字节分开记录。

| 配置 | 两臂相同值 | 含义 |
|---|---:|---|
| Packed 磁盘预算 | 256 MiB | off 配置相同但不使用整包磁盘缓存 |
| Packed 元数据 | 256 MiB | 元数据预算 |
| 普通 VFS read memory / SSD | 512 MiB / 0 | 不等同于 Packed 解码帧留存 |
| Packed 已解码 payload 留存 | 0 | `snapshot/pipeline.rs` 的需求管道仅持有在途结果 |
| roots / plans | 16 / 32 MiB | 现有挂载预算 |
| Control / Stored / Raw | 8 / 32 / 32 MiB | 整包任务沿用现有 admission 预算 |
| decode workspace / Output | 8 / 128 MiB | 现有解码及输出预算 |
| BrewFS client | 2 CPU / 8 GiB | 每臂独立 cgroup |
| RustFS | 2 CPU / 2 GiB | 每轮独占容器，共享本轮四臂 |

10,000 时 off/on 的进程 VmHWM 分别为 111,628–112,184 / 112,120–112,980 KiB；cgroup memory.peak 分别为 59,727,872–59,969,536 / 114,356,224–114,667,520 字节。后者包含归属该 cgroup 的文件缓存，已共享页的收费归属也会影响数值，不能当成进程 RSS。总下载约 48.6 MB，低于 256 MiB 磁盘预算；本轮没有验证超过预算后的稳态驱逐性能。FUSE direct IO 开启，keep_cache 为 false、TTL 为 0。

四个完整运行都通过所有文件的内容校验、清洁卸载及资源清理。保留缓存重挂后 `downloaded=0`，10,000 只剩挂载 manifest 的 1 次 Range GET / 5,495 字节。另用 Redis DB1 和独立 namespace 建立共享同一不可变快照的独立工作区，验证首次私有写、冷重挂及父数据不变。该检查是独立工作区隔离检查。private/private-cold 访问原 S3 endpoint、绕过性能代理，因此其 S3 probe 为 0 不表示零下载；10,000 的私有臂 cache 日志实际记录 48,574,587 字节下载，这些臂不进入上述性能统计。

运行仍有已知警告：10,000 的两臂日志周期性出现 workspace layer/orphan GC 的 `UnsupportedCapability("legacy GC cannot collect bucket-isolated workspaces; use explicit upper bucket cleanup")`，较长的 off 臂约 2 次、on 臂约 1 次。读回 SHA、退出及 harness 自有资源清理均成功；上述正确性通过不代表已经验证该 GC 能力。临时 RustFS 凭据按运行生成并在日志中脱敏，测试服务已销毁。归档只含本任务证据，不打包 SSH 私钥或云控制面配置；发布前的凭据模式扫描和逐文件校验回执见 `verification.json`。

**冻结身份与可复核证据。**

- 实测源码：`6912a796abddbd96c60aad6cc672fcd926a13712`，release 自报 dirty=false。原始结果中的 `base_commit=ca07159…` 是历史 fixture 的来源，实测源码以每次 invocation、binary-version 和 backend-version 的绑定为准。
- BrewFS SHA-256：`d60704479f511559d7b6211f5070eeeb0728e24c9dfb460af966a79f17e87456`。
- producer SHA-256：`2a0164001031a2ecf1372f4f3fdc54cb98e3ef44716329335b8f6148645eeb17`。
- RustFS 用户分支 `feat/dynamic-block-size-rebased`，commit `f4ed202508f2153afb7956bae6f7799b60eccd00`，镜像 ID/digest `sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806`。`backend-version.json` 机械核验最新分支、clean 源码、OCI revision、registry digest 及实际二进制 SHA；其 `runtime_verified=false` 表示静态来源核验，运行 harness 另核实际容器镜像 ID 和 OCI revision。dynamic 的四个开关启用，客户端 hint=auto；本对照不声称另行验证物理布局变化。
- Redis 实际使用本机独立进程，版本由原始 `redis_version` 记录为 7.0.15；原始 `image_sha256.redis` 是历史 fixture 遗留的未使用镜像引用，不表示本轮运行 Redis 容器。
- 128 输入 SHA：`885168de392fa6fd5368fcab0ad6ab83cd132a04c60cae96ec93b4305e7d775e`；10,000 输入 SHA：`a6be37e6b0af24208b2dd0f95a1bfb7546e9cab9bc30ee97523dd8a09d7f41cc`。
- 最终 harness SHA：`6ded783fcc8188aaa3c68d6d51f3ad7be19557828c501606d548f7148b0475ab`；meter SHA：`4646ca21451e1c92f62b7e917f78de158334632303eab4391ffb6a94b6c1e9d5`。四个完整运行使用同版本脚本。
- 后续 `f2542d9be485b0ea92b51f1980e7b7ca80572444` 只恢复历史文档/证据；`source-after-artifact-restoration.json` 核对 src、Cargo、build、docker、operator、tests 树对象不变。此提交没有热更新云机或改变实测身份。

构建和测量串行。release 第一轮 900 秒超时后复用 target，第二轮在 28 分 54 秒完成；CPUQuota 构建期间从 500% 调至 750%，测量始终固定 client 200% / RustFS 200%。编译前完成默认 CI 各门槛以及实际 feature 组合测试；最终命名后的测试记录在 `test-names-01.log`，2821 + 2 + 15 项通过。默认门槛首次因未装 clippy 中断，安装后 `clippy-01.log` 通过；失败尝试原样保留。最终默认测试为 1354 + 2 项通过。

`pair-128-01` 到 `04` 均保留为未完成资格的尝试：分别为 cache 环境变量泄漏到初始化、trace parser 未接受冒号、私有工作区复用 Redis DB、S3 尚未就绪返回 503。它们不进入统计。修正仅涉及 harness 初始化/采集；没有改变 Rust 二进制。另保留第一次未完成传输产生的 backend 回执，正确来源以 `backend-version.json` 为准，细节见 `attempt-notes.json` 和 `transfer-attempt-note.txt`。

归档分为原始结果与日志、派生统计和本文结论：[evidence.tar.gz](evidence.tar.gz) 保留 rbm 94 个文件的原始字节（包含失败回执及已标识的方法说明）；[raw-sha256.json](raw-sha256.json) 逐文件记录未压缩 SHA / 大小 / 类别；[artifact-index.json](artifact-index.json) 记录 gzip 归档 SHA 和 summary SHA。[summary.json](summary.json) 由 [analyze.py](analyze.py) 生成，保留全部臂、配对收益、请求、缓存、预算身份和正确性检查摘要。`provenance/` 提供少量直接可读的身份和方法回执，其字节与归档副本相同。

在独立目录复核归档与统计：

```sh
sha256sum evidence.tar.gz
mkdir unpacked
tar -xzf evidence.tar.gz -C unpacked
python3 analyze.py unpacked > recomputed-summary.json
cmp summary.json recomputed-summary.json
```

重跑需要已固定 SHA 的实际二进制、该 RustFS 镜像、FUSE、Docker、Python boto3 和 native Redis。使用当前 pilot 脚本，选择新的结果路径；先 128 的两个顺序，均有收益后再扩展 10,000。旧冻结 fixture 的文件及 SHA 不改动：

```sh
python3 experiments/unified-suite/pilot-aliyun/scripts/qualify-packed-cache.py \
  /absolute/new-campaign/pair-128-01.json \
  --binary /absolute/bin/brewfs --producer /absolute/bin/packed_snapshot_fixture \
  --files 128 --order off-on
```

第二轮用新的结果名及 `--order on-off`；10,000 用 `--files 10000`。新二进制需新 campaign、独立记录源码和产物身份，不能把其数据并入本候选统计。
