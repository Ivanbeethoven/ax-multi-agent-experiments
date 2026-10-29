# AI trace：来源、规模与回放方法

本轮 G1/A1 使用现有 **7 份固定 trace**，逐案例比较；不只测 pytest。精确 URI、归档大小和 SHA256 见 [输入目录](../scenarios/k8s-containers/archives.json)，OSS 对账见 [核查记录](../experiments/retest-2026-10-29/evidence/trace-inventory.json)。

## 1. 来自什么 benchmark

| case ID | 来源 / 具体任务 | 采集批次 |
|---|---|---|
| django | SWE-bench Verified：`django__django-10554` | 2026-10-23 |
| sklearn | SWE-bench Verified：`scikit-learn__scikit-learn-25102` | 2026-10-23 |
| sympy | SWE-bench Verified：`sympy__sympy-13878` | 2026-10-23 |
| pytest-full-02 | SWE-bench Verified：`pytest-dev__pytest-10356` | 2026-10-23 |
| pytest-20261026 | 同一道 Pytest 题的另一次真实运行 | 2026-10-26 |
| ripgrep-20261026 | 定制 Agent 任务：ripgrep 14.1.1，`cargo build/test --locked --workspace` | 2026-10-26 |
| archive-io-20261026 | 定制 Agent 任务：12,000 小文件、4×128 MiB 文件的归档、解包、扫描和 SQLite 索引 | 2026-10-26 |

因此是 **4 道 SWE-bench 题的 5 次采集＋2 个定制任务**，不是 7 个不同 benchmark，也不是 7 次独立的 SWE-bench 解题成功。两次 Pytest 分别展示，不把同题重复计为额外任务多样性。

旧批的 [采集入口](../scripts/run_opencode_full_trace.py) 使用 [SWE-bench 选择器](../scripts/run_swebench_opencode.py)：每个指定仓库选较难题，推理前固定，不按结果替换。数据集是 `SWE-bench/SWE-bench_Verified`，parquet SHA256 为 `030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25`。批次配置与运行时摘要保存在 OSS 的 `batch-metadata.tar.gz`。

旧批标记 DeepSeek-v4-flash＋OpenCode 1.19.4。新批报告记录 OpenCode 1.2.26，模型配置标识 `experiment/deepseek-v4-flash`、上游 `deepseek-flash`。新 Pytest 的 `selection.json` 仍写 1.19.4；其 `RUN-REPORT.md` 明确说明这是未修改采集脚本的硬编码标签，不能当作运行版本证据。此次已保留这个冲突和报告的解释，后续扩充须直接记录实际版本及二进制 SHA。

Django 达到原采集的一小时上限，仍保留正常收尾并通过当时文件回放的轨迹；Django/sklearn 的收尾恢复记录也保留。新批较早的 `io-unreplayable` 因回放不支持的 ioctl 失败，只有失败元数据，不列入这 7 个有效输入。既有任务评分、Agent 退出、文件回放验收分别记录；不将任意一种替代另外两种。

## 2. 数据量是否足够

下表是一次采集的实际 FUSE 字节；初态字节来自归档文件清单中 `initial/` 的普通文件，不含回放中新写入的数据。MiB = 2²⁰ B。

| case | 回调数 | 实际读 MiB | 实际写 MiB | 初态文件数 | 初态 MiB |
|---|---:|---:|---:|---:|---:|
| sklearn | 9,233,326 | 1,672.69 | 290.91 | 1,854 | 255.41 |
| sympy | 7,265,338 | 1,072.55 | 221.30 | 1,553 | 88.97 |
| django | 57,787,980 | 2,186.14 | 271.56 | 6,120 | 127.30 |
| pytest-full-02 | 1,747,109 | 268.72 | 185.69 | 614 | 33.88 |
| pytest-20261026 | 2,836,142 | 144.93 | 45.61 | 612 | 23.08 |
| ripgrep-20261026 | 3,122,195 | 4,187.70 | 1,123.51 | 213 | 2.89 |
| archive-io-20261026 | 3,673,814 | 1,699.47 | 1,716.04 | 3 | 0.01 |

**足够开展这 7 种已记录文件访问的配对比较；不足以单独证明广泛 Agent 性能、海量只读小文件或超内存存储性能。**

- 共 **85,665,904 次回调**，7 个压缩包 **10,407,035,011 B（9.69 GiB）**。压缩包包含事件流、写入 blob、初终态与回放证据；包大小不是工作集。
- Django 的 57.79M 回调中 55.51M 是 getattr，重复读取比例约 94.84%；大回调数主要增强元数据路径覆盖，不等于大量不同数据。其 events JSONL 本身约 21.36 GiB，会影响输入读取/解析成本。
- 旧 pytest 的数据量较小，只跑它不足以解释 BrewFS/JuiceFS 的总体差异。新增 Rust 与 archive-I/O 覆盖构建写入及低重复读 I/O；不能把所有案例的 ops 相加后只报一个吞吐。
- 当前最大初态也仅约 255 MiB，累计读写多次可能命中缓存。P 另用 1,048,576 个独立小文件；D 另用冻结对象池及明确缓存状态。不得循环重复 trace 冒充新任务或更大的独立数据集。

正式 G1/A1 每个 case 各做 3 个完整换位区组，重复从同一初态开始；重复度量运行变动，不增加任务样本数。新 benchmark 语料另在独立会话收集，验收并冻结后才进入新版本矩阵，不能在本批进行中临时替换案例。

## 3. 回放工具及计时

工具为仓库 [Rust full-fuse-replay](../tools/full-fuse-replay/README.md)，不是 OpenCode 再跑一次，也不是旧 v1 的 `fuse-workload-replay`。捕获 repo 和 HOME/XDG 上的高层 FUSE 回调及真实写入 blobs；原顺序串行回放，不重新启动原任务进程、模型或原调度。

```bash
"$REPLAYER" "$TRACE_CASE/capture/events.jsonl" "$TRACE_CASE/capture/blobs" \
  "$REPLAY_ROOT" "$RUN_DIR/replay.json" --performance
```

同一输入、写入内容、初始文件树、二进制与计时方式在全部 baseline 保持一致。Overlay 初态置于不可变 native lower，写入 upper；其它臂导入相同逻辑初态。**输入 events/blobs 放被测挂载之外**，两机使用相同存储位置/预读策略，记录输入读取及解析成本，避免将大 JSONL 输入盘的差异误归为文件系统差异。

首次资格验证做严格模式及独立内容检查；正式 `--performance` 保留全部操作与 xattr、操作/长度/通常 errno 检查，但省去逐操作内容/属性/目录比对。预期与实际均失败的 xattr/ioctl errno 差异单列。核 completed、总操作与读写字节、handles=0，另核最终树和重挂后的内容。**performance completed 不是严格正确性通过**。不设总回放时限；计时含 JSON 解析、blob 读取和实际系统调用，不含准备/计时外验收。

捕获未包含内核页缓存命中、本机锁处理、网络、镜像运行库及 `/tmp`；回放不重建原进程并发。它衡量已记录的文件访问工作量，不直接等于 Agent 端到端速度、正确率或训练吞吐。采集时的一小时任务限制不适用于当前完整回放。

## 4. 本次 OSS 完整性检查的边界

2026-10-29 使用当前 CLI 身份列出全部 3 个可见桶：trace 桶 28 对象，旧 TiKV 实验桶 239 对象，另一桶空。三个桶的 listing 都完整，无截断；另外两个桶未发现 trace 归档。trace 桶无未完成 multipart，整桶没有目录外的新归档。

7 个归档的 LIST/HEAD 大小、远端 SHA 元信息与两份本地目录一致；**7 份 sidecar 和两批小型 metadata 已下载并核 SHA**。每份 sidecar 均含 events、initial、blobs、initial-tree、final-tree、metrics、result、verification、replay，且有原始任务/Agent 记录可追溯。没有把失败 I/O 或仅 299 操作的预检当作有效完整任务。

此次未下载全部 9.69 GiB 完整包，未重新逐字节验证包内 payload，也未运行七份 trace。开测前须用 [下载器](../scenarios/k8s-containers/scripts/download_archives.py) 与 [归档校验](../scenarios/k8s-containers/scripts/check_archives.py) 下载并核整包 SHA，再做 [输入和结果检查](../harness/trace_checks.py)。远端元信息匹配不能替代本地完整哈希。

可独立重做本次只读核查：

```bash
python3 harness/trace_oss_audit.py --output "$NEW_AUDIT_DIR"
```

该脚本只读 trace 桶并下载 metadata/sidecar；账号全部桶的 CLI 盘点作为本次独立证据保存。完整单次回放命令与逐步检查见 [执行手册第 3 节](../infra/alicloud/RUN.zh.md#3-g1--a1一次真实-trace)。
