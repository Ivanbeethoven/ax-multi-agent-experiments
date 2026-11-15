# JuiceFS 端到端与六段占比报告

测量日期：2026-10-29。JuiceFS 源码为 v1.4.2（revision 46af7af），二进制 SHA-256 为
c1fa734a5bf7a3ad330472f8f2f2845ad85de82ead6a745efc4a7cbdf1163285。
源码改动完整 diff 保存在 [/work/juicefs-stage.patch](/work/juicefs-stage.patch)，哈希记录在
[/work/juicefs-binary.sha256](/work/juicefs-binary.sha256)。

## 端到端结果

吞吐单位为 MiB/s，p99 单位为 ms；每个单元按“min / median / max”排列。

| 臂 | W 吞吐 | W p99 | C 吞吐 | C p99 |
| --- | ---: | ---: | ---: | ---: |
| flat-v1 | 215.718 / 216.395 / 219.192* | 2.605 / 2.703 / 2.998* | 45.967 | 55.312 |
| overlay+native | 未完成（前序热态检查未通过） | 未完成 | 19.874 | 221.250 |
| overlay+packed | 未完成（前序产物不完整） | 未完成 | 9.686 | 480.248 |
| **JuiceFS** | **64.754 / 65.769 / 88.625** | **12.911 / 16.056 / 16.056** | **53.081** | **52.167** |

* flat-v1 的 W 数字来自前序 /work/stage-artifacts/run-flat-v1 产物，作为同机参考；其随机预热差 2.14%。overlay 两臂的 C 数字是任务给定的参考数字，前序脚本没有形成可用的完整 W 结果，因此没有补造数字。

JuiceFS 的 W 三次读请求数分别为 505,119、497,330、680,662；C 为 407,691。W 之间不清页缓存。C 前执行 sync 和 echo 3 > /proc/sys/vm/drop_caches，没有清除 JuiceFS 的本地块缓存。fio 使用 4 个 512 MiB 文件、4 KiB、libaio、iodepth=32、direct=1、30 秒；为保证四个 fio job 对应四个文件，额外显式设置了 filename_format=r.$jobnum。

语料来自 /work/stage-source/r.0 到 r.3，每个文件 536,870,912 字节。顺序预热一遍后，随机预热吞吐为 62.017 和 63.817 MiB/s，差 2.90%，满足小于 10% 的热态条件。前两遍随机读在重编译后的挂载上分别为 62.017 和 63.817 MiB/s。

## JuiceFS 六段（方法③，W 口径）

源码埋点使用环境变量 JFS_STAGE_TIMING=1 开启，关闭时不读取时钟。每个 W 轮的快照由 SIGUSR1 触发，按相邻快照相减；平均值为 sum_us / FUSE read count。占比以 fuse_dispatch 的平均墙钟时间为分母。

| 段 | 平均 µs / FUSE read（W 三次中位） | 相对 fuse_dispatch |
| --- | ---: | ---: |
| fuse_dispatch | 116.429 | 100.00% |
| resolve | 0.002 | 0.001% |
| row_cache | 0.015 | 0.013% |
| redis_meta | 0.106 | 0.091% |
| extent_plan | 7.980 | 6.854% |
| data_path | 139.568 | 119.874% |

fuse_dispatch 的计数与 fio 读次数一致。各段是并发读路径中的独立 wall-time span，多个 slice 的 data_path 可以同时执行，所以占比不要求相加为 100%；data_path 超过 100% 是并发重叠的结果，不是把吞吐换算成了额外时间。W 三次的原始平均值如下：

| 轮次 | fuse_dispatch | resolve | row_cache | redis_meta | extent_plan | data_path |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| W1 | 116.429 | 0.004 | 0.015 | 0.112 | 7.980 | 139.568 |
| W2 | 118.452 | 0.002 | 0.032 | 0.106 | 8.149 | 140.850 |
| W3 | 89.065 | 0.001 | 0.012 | 0.074 | 5.492 | 111.317 |

埋点位置：

- fuse_dispatch：pkg/fuse/fuse.go 的 fileSystem.Read。
- resolve：pkg/meta/base.go 的 Lookup。
- row_cache：GetAttr 的 open-cache 检查和 Read 的 open-file chunk/slice cache 命中。
- redis_meta：pkg/meta/redis.go 的 doLookup、doGetAttr、doRead。
- extent_plan：pkg/vfs/reader.go 的 prepareRequests 区间规划。
- data_path：pkg/chunk/cached_store.go 的 rSlice.ReadAt，包含块缓存查找和对象读取等待。

程序退出或收到 SIGUSR1 时写出 JFS_STAGE_TIMING_PATH 指定的 JSON；本次文件为 /work/jfs-stage-timing2.json。退出时通过 main 的 defer 再写一次。

## 计数器方法②

JuiceFS Prometheus metrics、Redis DB3 的 INFO commandstats 和 RustFS 侧 tcpdump 交叉检查如下。Redis 的 raw 总数包含快照本身产生的 INFO/SELECT，括号内为减去这两个快照命令后的值。

| 轮次 | fio 读数 | Redis 命令增量（raw / 校正） | Redis 命令 / 读 | EVALSHA / 读 | 对象 GET / 读 | 块缓存命中 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| W1 | 505,119 | 183 / 181 | 0.000362 / 0.000358 | 7.92e-6 | 0 | 519,896 |
| W2 | 497,330 | 199 / 197 | 0.000400 / 0.000396 | 8.04e-6 | 0 | 511,840 |
| W3 | 680,662 | 183 / 181 | 0.000269 / 0.000266 | 5.88e-6 | 0 | 700,187 |
| C | 407,691 | 183 / 181 | 0.000449 / 0.000444 | 9.81e-6 | 0 | 419,461 |

W 中位数为 0.000362 raw Redis 命令/读、7.92e-6 EVALSHA/读和 0 GET/读。Redis 差值包括挂载客户端的后台会话维护，因此 EVALSHA 单列用于和 brewfs 对表。每个读的块缓存命中数略高于 FUSE 读数，是 4 KiB 请求跨块边界时的多个块访问。

对象侧有两次独立验证：JuiceFS 的 juicefs_object_request_durations_histogram_seconds_count{method="GET"} 在每个区间增量为 0；额外 30 秒随机读期间用 tcpdump 监听 lo:9000，捕获 0 个包（pcap 为 24 字节空文件）。原因是本轮本地块缓存已经热，未覆盖冷对象 GET 路径。

juicefs stats /mnt/jfs --schema ufmco -l 1 -c 3 的输出保存在
[/work/jfs2-stats-final.txt](/work/jfs2-stats-final.txt)，显示读期间对象 get 和 blockcache miss 均为 0。

## pprof 方法①

v1.4.2 没有任务示例中的 --profile-dir 参数；它的 pprof debug agent 在挂载子进程的 6061 端口。W 辅助轮期间从 /debug/pprof/profile?seconds=30 抓取
[/work/jfs2-prof-cpu-child.pprof](/work/jfs2-prof-cpu-child.pprof)，再运行 go tool pprof -top -cum。该 profile 时长 30.11 秒、CPU sample 累计 134.34 秒（多核）。

函数归类的主要结果是：

- data path：dataReader.readSlice 累计 93.32 秒（69.47%），rSlice.ReadAt 累计 92.90 秒（69.15%），其中包含 cacheFile.ReadAt。
- FUSE dispatch：pkg/fuse.(*fileSystem).Read 累计 13.82 秒（10.29%）。
- extent/reader：fileReader.prepareRequests 4.07 秒（3.03%），splitRange 0.91 秒（0.68%）；外层 fileReader.Read 为 9.48 秒（7.06%）。
- resolve、row-cache、Redis 元数据函数在本次热缓存 CPU profile 的显示阈值（约 0.67 秒 cumulative）内没有单独出现。

pprof 的 cumulative 行彼此嵌套，不能把这些百分比相加；它测的是 CPU sample，不是 wall time，阻塞在块缓存或对象存储上的时间不会按等待时长计入。

## 关键参数

| 参数 | 本次值 |
| --- | --- |
| version | 1.4.2+2026-10-22.46af7af |
| metadata engine | Redis redis://127.0.0.1:6379/3 |
| storage | S3-compatible RustFS http://127.0.0.1:9000/juicefs-loss |
| cache-dir / cache-size | /work/jfs-cache / 4096 MiB |
| writeback | false |
| format block-size | 4096 KiB（4 MiB） |
| internal chunk-size | 64 MiB |
| attr-cache / entry-cache | 默认 1.0s / 1.0s |
| open-cache | 默认 0s |
| direct I/O | fio direct=1 |
| prefetch/readahead | 未额外改变默认值 |

没有使用提高数字的非默认缓存参数，也没有修改 writeback 语义。DB0、DB1、DB2 只做了只读 DBSIZE 检查；JuiceFS 元数据和计数全部在 DB3。

## 局限与未验证项

方法③的 span 在 slice 并发执行时会重叠，六段占比用于定位成本来源，不能当作互斥时间分解；resolve 只覆盖 base Meta 的 Lookup，特殊 inode 和部分缓存路径不经过该钩子；row_cache 覆盖 open-file chunk/slice cache 和 open-cache 探针，entry-cache 的 miss/命中没有单独拆成两类。关闭埋点的正式对照吞吐没有另跑一轮，因此只确认了关闭路径没有时钟调用，未给出完整的仪器化百分比。

pprof 只说明 CPU 消耗，不能替代墙钟分段。对象 GET 本轮为零，尚未验证清空 JuiceFS disk cache 后的冷对象读取、重试和对象延迟。未验证多客户端并发、其他元数据引擎、writeback=true、非 4 MiB block-size，以及完整 --profile-dir 采集流程。overlay+native/packed 的前序 W 结果没有满足热态和完整产物条件，表中保留为未完成。
