# 完整 OpenCode 任务：采集和回放

已完成一次真实 OpenCode 运行：`pytest-dev__pytest-10356`，`deepseek-v4-flash`，不设步数上限，1 小时超时保护。记录任务整个进程生命周期中的仓库和所有 HOME/XDG 目录。新增入口是 `run_opencode_full_trace.py`，旧的只记 Agent 事件的入口仍默认拒绝启动。

## 为什么改为挂载层

旧 strace 转换器只能处理单进程，不能直接接 OpenCode 的子进程、SQLite WAL 和 mmap。新方式把仓库和状态目录放在一个 FUSE passthrough 挂载中。所有相关进程通过这个挂载访问文件，内核产生的映射读写也会到达记录器。

这是高层 FUSE 回调记录，不是应用系统调用日志，也不是逐个内核 FUSE opcode 的原始协议包。内核已经命中的页缓存、本机锁处理、容器镜像中的运行库、网络、`/tmp` 不在记录中。任务没有被拆成少量工具步骤，但也不声称记录了整台机器的全部 I/O。

## 开跑顺序

1. 小预检实际执行子进程、SQLite WAL、mmap 读写、目录遍历、rename、软/硬链接、chmod。
2. Rust 回放预检，比较返回内容、错误码、最终文件树；通过后写 gate。
3. 验证原始代码不能通过目标测试、参考修复能通过。参考补丁只在独立评分容器使用。
4. 冻结初始仓库和状态。状态缓存复用之前运行的 XDG cache，但 HOME 和会话数据库从空目录开始，仍会安装依赖。
5. 挂载记录器，把 `repo` 和 `state` 分别绑定到容器的 `/testbed` 与 `/agent-state`，检查两条路径确实为 FUSE，再启动 OpenCode。记录器退出时停止任务。
6. OpenCode 完成后停止整个容器，关闭残留句柄与映射，再正常卸载。保留完整日志、写入内容、Agent 事件和最终状态。
7. 在新的初始副本中用 Rust 回放。比较读取内容、返回长度、错误码、路径、文件内容和权限；检查会话数据库；再独立评分 Agent 补丁。

模型凭据只在宿主机转发器中使用，Agent 收到本地转发地址和无密钥占位值。模型密钥不进入 Agent 环境、配置或文件记录。发布前仍扫描全部数据中的已知凭据，发现即停止，不修改实际读写字节掩盖问题。

## 执行新采集

当前脚本绑定本机已有环境：WSL Ubuntu 24.04、Docker、SWE-bench 数据和镜像、OpenCode 1.19.4。需要 `fusepy==3.0.1`、libfuse 2.9.9、`/dev/fuse`，且 `/etc/fuse.conf` 允许 `user_allow_other`。构建 Rust 入口：

```bash
CARGO_TARGET_DIR=/home/hxy/ax-full-trace-build cargo build --release --locked \
  --manifest-path tools/full-fuse-replay/Cargo.toml
```

从 PowerShell 将已有配置经 stdin 传入，密钥不要写入命令参数：

```powershell
$taskConfig = Get-Content "$env:USERPROFILE/.config/opencode/opencode.json" -Raw | ConvertFrom-Json
$taskConfig.provider.deepseek | ConvertTo-Json -Depth 12 -Compress |
  wsl -d Ubuntu-24.04 -- /home/hxy/research/agent-fuse-swebench/.venv/bin/python `
    /mnt/c/<实验仓库路径>/scripts/run_opencode_full_trace.py `
    --output /home/hxy/ax-full-trace/<新目录>
```

这条命令会运行一个真实模型任务。只验证现有数据、不调用模型时，用 [结果目录](../results/opencode-full-fuse-pytest-10356-20261023/README.md) 中的独立回放命令。

## 数据和统计

`capture/events.jsonl` 每行一个完整回调，序号连续；`capture/blobs/<sha256>` 保存实际写入内容，按摘要复用。正常记录从 init 到 destroy。读取保存返回长度和 SHA256，写入保存内容对象与返回长度。没有抽样，也没有填造写入数据。

`analyze_full_fuse_trace.py` 流式统计操作次数、成功返回的读写字节、错误码、重复读取、仓库/状态分项。重复读取按文件身份和偏移计算：已有读取范围再次读取计重叠，覆盖写使对应范围失效，rename/link 保留身份，删除重建另算。它不是页缓存命中率。统计中的读写量是挂载层字节，不是设备或云端物理流量。

目录缓存超时设置为零，记录器单线程处理回调，回放也是串行。这样可以确定已观察文件操作的顺序，但会影响原任务的时间、元数据请求数和并发。此数据用于证明采集与回放正确，尚不能直接作为默认生产配置下的性能结论。Rust 本版的执行时间包含 JSON 解析和内容校验，不能拿 8 秒回放与 173 秒 Agent 运行相比来声称存储加速。

getattr 的 inode、设备号、所有者、时间戳及目录分配大小不要求跨机器相同；statfs 容量也不作相等要求。最终校验范围是内容、路径、链接目标和权限；本次还核对最终硬链接关系和 SQLite 数据库完整性。回放不会重新调用模型，也没有验证移机后的 Agent resume。

## 发布和独立回放

`bundle_full_trace.py` 扫描凭据后，把初始树、全部回调、全部写入载荷、结果及预检压缩，按 20 MiB 分卷；`bundle.json` 保存各卷和整体的 SHA256。使用普通 Git 文件，不依赖 Git LFS。

`replay_full_trace_bundle.py` 重组并校验分卷，安全解压、保留普通权限，验证初始树，调用 Rust，最后验证最终树与统计。这一步不需要 OpenCode、模型密钥、原容器或原始运行目录。
