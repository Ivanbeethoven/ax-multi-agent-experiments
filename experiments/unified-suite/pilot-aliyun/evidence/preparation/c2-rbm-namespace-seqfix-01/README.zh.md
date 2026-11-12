# C2：Packed lower 热读对照在 WSL 通过

同一个 BrewFS 二进制在同一台 WSL 上，依次挂载同一份 Packed lower 的两种只读模式。每组都先读冷数据、再验证热读，然后运行 128 轮同样的读取、查找、属性查询和目录操作。两组均通过。两组都拒绝写打开（`EROFS`），都读取同样的两个文件并核对内容。测试结束后挂载、Redis、RustFS、临时目录均已清理。

| 计时阶段 | 查询上层 `overlay-read` | 跳过上层 `lower-read` |
| --- | ---: | ---: |
| FUSE 读取次数 / 字节 | 256 / 9,856 | 256 / 9,856 |
| RustFS 请求 / 字节 | 0 / 0 | 0 / 0 |
| 上层 extent 查询 / 跳过 | 256 / 0 | 0 / 256 |
| 上层查找缓存命中 / 跳过 | 641 / 0 | 0 / 641 |
| 上层属性缓存命中 / 跳过 | 3,328 / 0 | 0 / 3,328 |
| 上层目录检查 / 跳过 | 770 / 0 | 0 / 770 |
| 128 轮耗时 | 1.329 秒 | 0.981 秒 |
| 单次 `pread` 中位数 | 901.5 µs | 347.4 µs |
| 打开并列目录中位数 | 3,779.9 µs | 2,826.6 µs |
| 查找并取属性中位数 | 495.8 µs | 453.9 µs |

两个冷读阶段均由代理实测 7 次 RustFS Range GET、13,409 字节；热读验证和计时阶段均为零。原生 Packed 计数同样显示计时阶段没有远端读取，仍完成 9,856 字节的逻辑读取。计数证明两组走了不同的上层路径，FUSE 没有被页缓存绕过。本次只有两个文件和一次固定顺序的对照，不能用于估计完整 trace 或云上 C2 的收益；未计入正式单元。

修正只接纳首次绑定后 `head.next_sequence=2` 且 `binding_version=1` 的初始状态，保留空上层、根目录占位和租约检查。[上次拒绝](../c2-rbm-namespace-resume-01/README.zh.md)发生在这个前置检查。新二进制基于 BrewFS main `ca07159f3da4b670e4aeb878faef928e69bc1634`、[原补丁](../../../patches/ax-brewfs-c2-hot-read-namespace-ca07159.patch)和[修正补丁](../../../patches/ax-brewfs-c2-initial-binding-sequence-followup.patch)。[构建回执](raw/terminal-observed.txt)记录 08:00:56–08:07:59 UTC、退出码 0；结束后工作树恢复为原候选。二进制 SHA-256 `2be1443254b8092e21693a11f50d5b3423db1ea4f4ca6a6ca372c59c15fef09c` 已在 rbm、Windows 和 WSL 核对。[RustFS 来源回执](raw/backend-version.json)记录开跑前最新分支提交 `f4ed202508f2153afb7956bae6f7799b60eccd00` 与镜像 revision 相同。没有启用阿里云资源或模型。

[实际命令](raw/smoke/argv.json) · [原始结果](raw/smoke/result.json) · [计数和延迟摘要](raw/summary.json) · [文件 SHA](SHA256SUMS) · [解压后的原始 .stats SHA](RAW-STATS-SHA256SUMS)

下一步在 10 万文件和固定 trace 上轮换两组顺序并重复，继续要求两组在计时期间均无后端读取。
