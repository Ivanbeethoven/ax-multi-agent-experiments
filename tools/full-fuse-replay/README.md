# 完整任务的挂载请求回放

可选 [LLM 等待模拟](THINK-TIME.zh.md)：默认关闭，只在多 Agent 回放显式使用 --simulate-llm PLAN.json；工具/轮次边界每段 0–15 秒，计划总等待固定，直接性能命令保持无等待。

这个 Rust 入口读取 `full_fuse_record.py` 产生的高层 FUSE 回调记录。它是完整任务试跑的扩展入口；原独立 `fuse-workload-replay` 的 v1 输入格式和 27 次操作记录保持原样。

在本环境指定专用的绝对构建目录 `REPLAY_BUILD_DIR`，记录到本轮构建清单；从实验仓库根目录执行：

```bash
CARGO_TARGET_DIR="${REPLAY_BUILD_DIR:?set an absolute build directory for this run}" cargo build --release --locked \
  --manifest-path tools/full-fuse-replay/Cargo.toml
```

直接调用格式：

```text
full-fuse-replay EVENTS.jsonl BLOBS_DIR DEDICATED_ROOT NEW_OUTPUT.json
```

性能回放增加 `--performance`，仍使用同一 Rust 入口并执行全部已记录操作：

```text
full-fuse-replay EVENTS.jsonl BLOBS_DIR DEDICATED_ROOT NEW_OUTPUT.json --performance
```

此模式仅适用于已预检、摘要匹配的可信 trace 和无其他写者的专用目录。它省去逐次路径 canonicalize、内容 SHA256、属性和目录结果比对；保留输入序号、操作类型、写入载荷长度、读写返回长度和通常的 errno 检查。getxattr/listxattr/ioctl 仅在预期和实际都失败时允许 errno 不同，差异数单独记录；不会跳过这些操作，也不会忽略 opendir/read/write 的意外失败。

性能报告标为 `mode=performance`、`completed=true`、`valid=false`。`completed` 表示处理到正常 destroy；实验还须核对总操作数、读写字节和剩余句柄为零，不能将其表述为严格正确性通过。性能模式不做最终文件树验证，默认严格模式仍保留原检查。计时仍包含 JSON 解析、载荷读取及实际系统调用；该入口没有总回放时限。

通常应使用 `scripts/replay_full_trace_bundle.py`，由它先核验结果包和初始文件树，再调用 Rust，最后核验整个文件树。

每行记录包含连续 `seq`、`op`、参数、结果、errno、请求 PID、UID/GID 和单调起止时间。写入内容保存在以 SHA256 命名的 `blobs/` 中，参数引用摘要和长度；读取保存返回长度和内容 SHA256。没有用合成字节代替真实写入。

回放按记录顺序实际执行文件操作。打开句柄持续保留，支持 rename/unlink 后继续使用句柄；目录读取比较名字列表，文件读取比较长度和 SHA256，写入验证内容对象摘要和实际返回长度，失败请求比较 errno。新输入必须以 init 开始、正常 destroy 结束，序号断裂、未知操作、缺失载荷或内容不符都失败。

getattr 校验类型、权限以及非目录文件大小；设备号、inode 数值、UID/GID、时间戳、目录分配大小不作跨目录相等要求。statfs 验证调用成功，容量等动态返回值不作一致性要求。TCGETS 用实际 ioctl 检查预期 ENOTTY。最终校验覆盖路径、文件内容、链接目标和权限，SQLite 另做完整性核验。

这里是**串行的已记录挂载工作量回放**。捕获时内核已把子进程和 mmap 的 I/O 交给挂载点，Rust 不再启动这些进程或重建原内核调度、页缓存命中与本机文件锁。读写结果一致不能证明完整进程调度或锁语义被重演，也不能替代 Agent 的 resume 测试。

默认严格模式用于功能验收：运行时间包含流式 JSON 解析、内容校验和文件操作。严格模式的路径解析阻止访问指定 root 之外，但这不是用于执行恶意输入的完整沙箱；性能模式省去了这部分动态路径核查，两种模式都只使用经摘要核验的可信输入和专用、没有其他写者的目录。
