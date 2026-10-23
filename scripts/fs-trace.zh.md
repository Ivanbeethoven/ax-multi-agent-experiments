# 文件操作采集与 Rust 回放

本文保留单进程 strace 原型的使用方法。完整 OpenCode 任务现已通过另一条挂载层路径验收，见 [完整任务采集与回放](full-fuse-trace.zh.md)。不要用本原型的能力范围代替新入口的说明。

当前已经跑通一个小范围的真实文件操作流程：使用 `pytest-10356` 的原始仓库，读取源文件和测试文件，执行之前 Agent 产出的修复，再读取、检查写入结果和 Python 语法。没有调用模型，也没有把这段固定脚本称为整场 Agent 轨迹。完整测试套件、OpenCode 会话数据库和依赖目录不在本次采集范围内。

## 先检查记录器，再执行任务

在 WSL 中运行，输出目录必须是新目录：

```bash
CARGO_TARGET_DIR=/home/hxy/ax-fs-trace-build cargo build --release --locked \
  --manifest-path /path/to/fuse-workload-replay/Cargo.toml

/home/hxy/research/agent-fuse-swebench/.venv/bin/python \
  scripts/run_fs_trace_pilot.py \
  --output /home/hxy/ax-fs-trace/new-run \
  --binary /home/hxy/ax-fs-trace-build/release/fuse-replay
```

依赖：Linux、strace、现有 SWE-bench Docker 镜像、Python Docker SDK、Rust 回放程序。任务镜像来自已有 Pytest 评分记录中的内容 ID。脚本不下载新模型、不调用 API。

执行顺序固定如下：

1. 在专用空目录执行已知字节数的检查：短读、EOF、重叠读取、部分覆盖、rename 后继续读、ENOENT。
2. 检查采集结果必须是读 10 字节、写 5 字节、重复读 5 字节。
3. 在另一个目录让 Rust 回放；核对读写字节、操作数和最终文件树。
4. 写入 `preflight-gate.json`。只有前三步通过，才准备任务仓库并执行固定工具流程。
5. 对任务再次核对实际字节数、操作数、每次读取内容 SHA256 和最终树摘要。修复后的源文件还须与此前通过评分的 Agent 补丁完全一致。

脚本保留原始 `strace.log`。解析失败、空日志、内容截断、找不到打开的 FD、FD 未关闭、未知工作区操作或回放不一致都会报错，不会产生成功结果。发生失败时保留目录，不覆盖后重试。

旧 `run_swebench_opencode.py` 默认禁止新模型运行，因为它还没有完整文件系统记录器。`--allow-agent-events-only` 只用于明确接受旧式记录的复现，不是采集开关。

## 统计口径

- **实际读取字节**：工作区内 `read/pread64` 成功返回字节数之和；短读按返回值计，EOF 计零。
- **实际写入字节**：`write/pwrite64` 成功返回字节数之和。另存请求字节数，不能混用。
- **操作次数**：原始工作区系统调用按种类统计。`lseek` 保留在原始统计中；回放使用显式偏移的 `pread/pwrite`，因此回放操作数扣除 seek。预检覆盖了这种区别。
- **重复读取比例**：同一个文件已读过且此后没有被覆盖的字节，再次读取的字节数 ÷ 实际读取总字节数。按区间算交集；部分覆盖只使相应区间失效，rename 保留文件身份，删除后重建算新文件。它不是缓存命中率，也不是按内容跨文件去重率。

读内容保存 SHA256，写请求保存确切字节 `data_hex`。实际调用在日志中保留 PID、实时时钟时间和耗时；控制端另存单调时钟总耗时。这里没有把 strace 实时时钟称为单调时间，也不使用它重建并发。

原始调用和 Rust 额外的路径安全调用分别计数。操作实际经过文件系统，但**返回字节不等于物理磁盘、网络或对象存储流量**。本次在 WSL ext4 上验证；不是 FUSE 协议请求采集。指纹检查会暖缓存，strace 也有开销，不从这个小试跑得出吞吐性能结论。

## 当前支持范围

单进程、UTF-8 路径、明确工作区根目录；支持普通文件的 open、close、read/write、pread/pwrite、seek、stat、fsync/fdatasync、ftruncate、mkdir、rename、unlink/rmdir 等转换。只接受明确列出的 open 标志。每次 I/O 不超过 1 MiB。

在工作区内遇到 mmap、readv/writev、目录枚举、链接创建、锁等未接通操作，会拒绝转换；fork/clone、改变工作目录或无法解析的行同样拒绝。工作区外的 Python 运行时和库操作只报告排除计数，不混入工作区指标。记录的前提是没有其他不受跟踪的进程修改该专用目录。

不能用这版直接声称已采完任意 Agent。下一步须接通真实工具的多进程和目录枚举，再单独处理 OpenCode SQLite/WAL、锁与 mmap；每增加一类操作，都需要内容及最终状态校验。

## 文件

- `fs_trace_capture.py`：系统调用采集、严格转换、字节与重复读取统计。
- `fs_trace_workload.py`：已知结果预检，以及本次固定修复步骤。
- `run_fs_trace_pilot.py`：预检、任务、独立 Rust 回放和一致性检查。
- `../tests/test_fs_trace_capture.py`：空日志、截断、短读写、未知操作等检查。
- [已完成的记录](../results/fs-trace-pytest-10356-20261023/README.md)。
