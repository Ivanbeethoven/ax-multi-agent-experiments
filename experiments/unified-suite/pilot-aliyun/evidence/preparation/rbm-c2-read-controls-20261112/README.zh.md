# rbm：C2 只读模式接入真实运行入口

`bench.experiment_smoke --experiment C2-runtime --mode brewfs-full` 和 `--mode overlay-off` 都已通过。两个 flag 分别执行候选的 `overlay-read`、`lower-read`；额外用 `qualify-packed-read-local.py --arm both` 在同一个父工作区连续验证两组，也通过。三个成功运行均正常卸载并清理临时 Redis、RustFS 和目录。

输入为 2 文件、77 B。每组第一遍实际读取 RustFS 7 次 Range GET、13,409 B；第二遍仍通过 FUSE 读取 2 次、77 B，RustFS GET/HEAD/Range 和字节增量都为 0。原生计数与独立代理计数一致；每组均验证正确的上层查询/跳过路径，写打开得到 EROFS。

复用[主线已验证的 C2 候选](../c2-rbm-namespace-seqfix-01/README.zh.md)，二进制 SHA256 为 `2be1443254b8092e21693a11f50d5b3423db1ea4f4ca6a6ca372c59c15fef09c`。本轮没有重编译。新入口支持普通 Linux 主机的本地 Redis 和已固定来源的 RustFS 镜像，不再依赖 WSL 的绝对路径。BrewFS 产物须与 `--brewfs-provenance` 的已通过摘要一致。

首次探针在创建父工作区前失败：临时桶前缀加上 BrewFS 自动附加的 UUID 后超过 S3 桶名长度上限。已缩短前缀，失败原记录保留；后续运行使用新目录。

- [模式及计数摘要](summary.json)
- [完整方案入口与命令](overlay/receipt.json) · [跳过上层入口与命令](lower/receipt.json)
- [同一工作区双模式结果及实际 argv](same-workspace-pair/receipt.json)
- [首次失败](failed-bucket-name/run/receipt.json)
- [全部文件校验](SHA256SUMS) · [解压后 .stats 校验](RAW-STATS-SHA256SUMS)

这是只读查询、非根属性、目录打开及数据 extent 的最小功能验证。本轮不计时，不表示可写 Overlay 消融、完整 trace、Pack 开关或正式性能已经通过。不同子项的候选产物仍需分别指定，不能把当前矩阵当作同一二进制的全套验收。
