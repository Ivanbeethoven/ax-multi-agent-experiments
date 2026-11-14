# 并发替换修复后的 Overlay 热读复测

在同一台 Linux 服务器比较三个挂载：修复前的可写 Overlay、修复后的可写 Overlay、修复版直接挂载只读 Packed lower。Overlay 的上层为空；三个挂载共享同一个不可变 Packed 快照。并发替换的正确性检查见[单独结果](../overlay-enoent-a355-20261114/README.zh.md)。

| 文件数 | 每次读取量 | 每个挂载的计时轮数 | Packed 数据缓存 |
|---|---:|---:|---:|
| 2 | 77 B | 257 | 8 MiB |
| 10,000 | 40,960,000 B | 3 | 64 MiB |

每组重复三次，三个挂载轮换执行位置。每轮打开目录并列出目录项，然后顺序打开文件、读取属性、读完内容、关闭文件。逐文件核对实际字节；10,000 文件组单独检查包含 9,999 个文件的子目录确实分页读取。不同规模独立比较。

每次重新挂载，先读取相同输入，计时前核对缓存容量。FUSE 属性和目录项 TTL 均为 0，读取使用 direct I/O。三个挂载均设 256 MiB Packed 索引缓存；可写 Overlay 另有 64 MiB 上层行缓存，元数据提交使用 Redis。RustFS 只监听本机；两组计时均核对 BrewFS 的原生对象读取请求和响应字节增量。Redis `INFO commandstats` 记录实际执行命令，统计时排除 `INFO`，其中包括 Lua 内部执行的命令。

## 实测结果

A366 有 128 个逻辑 CPU。开始前约有 245 GiB 可用内存和 664 GiB SSD 空余，无其他编译、存储测试或容器运行。使用 Rust 1.99.0、Redis 7.0.15 和本机 RustFS。两组各完成 9 次挂载，全部通过文件内容检查，测试进程与挂载均已退出。

下表为各列三次运行的中位数，耗时单位为秒，百分比据相应中位数计算。

| 文件数 | 修复前 Overlay | 修复后 Overlay | 只读 lower | 修复后 / 修复前 − 1 | 修复后 / lower − 1 |
|---|---:|---:|---:|---:|---:|
| 2 | 0.8514 | 0.8433 | 0.6559 | −0.95% | +28.56% |
| 10,000 | 30.0022 | 29.5919 | 27.0726 | −1.37% | +9.31% |

两文件组的修复前后变化为 −1.59%、−0.26%、−2.19%；一万文件组为 +2.29%、−1.84%、−1.77%，没有观察到稳定的加速。一万文件组每次都走完目录分页，分页检查记录了 100 次 `getdents64`（含最后一次返回空结果）。一万文件组的剩余开销主要在 `open`：修复后累计中位数 17.172 秒，只读 lower 为 15.514 秒。两文件组反复打开和列出目录，这两项合计约多用 129 ms。

18 次计时的对象读取请求和响应字节均为 0。每个规模的两个 Overlay 版本均未触发上层区间查询或目录查询；两文件组有 514 次空区间缓存命中，一万文件组有 30,000 次，行缓存未命中均为 0。Redis 命令数中位数在修复前后相同：两文件组 2,056，一万文件组 737；只读 lower 全部为 0。修复没有改变这些查询次数。这个结果适用于已预热、上层为空的挂载。

[逐轮统计和阶段耗时](results/summary.json) · [两文件逐次记录](results/two-files-trials.ndjson.gz) · [一万文件逐次记录](results/many-files-10000-trials.ndjson.gz) · [修复前构建](results/before-build.json) · [修复后构建](results/after-build.json)

## Native 对照

用同一个修复版 BrewFS 比较两种可写卷：`native` 是直接挂载的 `flat` 卷；`native-overlay` 由 `workspace init-volume` 创建，数据写在工作区的可写层。两边都不使用 Packed。每组各运行三对，交替顺序。每个挂载独立导入相同文件，逐文件同步后正常卸载，再重新挂载并预热。导入、卸载和预热均不计时。

两边使用 Redis、4 MiB 数据块和分块、256 MiB 客户端读内存、禁用读 SSD 缓存和预读、FUSE TTL 为 0，并通过 direct I/O 验证计时读操作到达 BrewFS。各挂载使用独立 Redis 数据库和对象桶。

A672 有 104 个逻辑 CPU，测试开始前约有 118 GiB 可用内存和 755 GiB SSD 空余，无其他测试进程或容器。以下耗时为同一台机器上各路径三次运行的中位数：

| 文件数 | native | native-overlay | native-overlay / native − 1 | 三对差值 |
|---|---:|---:|---:|---|
| 2 | 0.5665 秒 | 0.9303 秒 | +64.22% | +58.17%、+64.22%、+51.89% |
| 10,000 | 25.6916 秒 | 23.3124 秒 | −9.26% | −13.24%、−7.57%、−1.55% |

两文件组中，打开并列出目录的累计中位耗时由 144 ms 增至 425 ms。一万文件组的目录操作也更慢（393 ms → 1,829 ms），但 `pread` 的累计中位耗时减少了 3.25 秒（7.226 秒 → 3.976 秒）。这组对照衡量完整挂载路径的差别，包含两种元数据实现及其缓存行为。

12 次计时均无对象读取。Redis 命令数中位数分别为 2 → 4,626 和 120,707 → 182,138；统计包含 Lua 内部执行的命令。A672 的 native 比较独立于 A366 的 Packed 比较。

[逐轮统计](native-results/summary.json) · [两文件逐次记录](native-results/two-files-trials.ndjson.gz) · [一万文件逐次记录](native-results/many-files-10000-trials.ndjson.gz)

## 复测命令

需要有充足空余内存和 SSD 空间的 Linux 机器、已安装的 Docker/FUSE/Redis、包含 boto3 的 Python 环境和离线 Cargo 依赖。先准备对照源码 `fa630c33ed9691f66b09742ae695aecb0d666a0e` 和修复源码 `187f62dd2dc820dfdab8148bf70419eb587befa1`。在测试机的仓库根目录运行：

```bash
python3 experiments/overlay-enoent-hotread-20261114/run_host.py \
  --before-source /work/brewfs-before --after-source /work/brewfs-after \
  --out /work/new-run --toolchain /path/to/rust/bin --cargo-home /path/to/cargo-cache \
  --rustfs-image ax-idcai/rustfs:f4ed202508f2153afb7956bae6f7799b60eccd00 \
  --lock /srv/ax-idcai-20261113/measurement.lock \
  --lock /srv/ax-idcai-20261113/build.lock --jobs 12
python3 experiments/overlay-enoent-hotread-20261114/summarize.py /work/new-run/two-files
python3 experiments/overlay-enoent-hotread-20261114/summarize.py /work/new-run/many-files-10000
```

`run_host.py` 分别构建两个版本的优化 release，记录命令和构建输出，并在两组测试前后记录资源占用。结果目录含每次挂载的原始调用耗时、配置、完整计数、执行顺序和服务退出状态。耗时开销按同组 `被测耗时 / 对照耗时 − 1` 计算，正值表示耗时增加。

已有修复版构建时，可以直接复测两个 native 路径：

```bash
python3 experiments/overlay-enoent-hotread-20261114/run_host.py \
  --native --reuse-builds /work/new-run --out /work/native-run \
  --rustfs-image ax-idcai/rustfs:f4ed202508f2153afb7956bae6f7799b60eccd00
python3 experiments/overlay-enoent-hotread-20261114/summarize_native.py /work/native-run/two-files
python3 experiments/overlay-enoent-hotread-20261114/summarize_native.py /work/native-run/many-files-10000
```
