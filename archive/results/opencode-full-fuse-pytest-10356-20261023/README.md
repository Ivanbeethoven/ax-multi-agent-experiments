# 完整 OpenCode 任务的文件采集与回放

**采集和 Rust 回放成功。**这次实际启动 OpenCode，让它从原始仓库独立处理 `pytest-dev__pytest-10356`，记录整个运行期间的仓库和 HOME/XDG 状态；没有替换成固定工具脚本，也没有只挑选少量操作。

OpenCode 1.19.4，模型 deepseek-v4-flash，不设步数限制，超时保护 1 小时。实际 25 步、173.5 秒，正常结束。**模型补丁没有通过目标测试 `test_mark_mro`，79 项回归测试通过。**任务修复失败与文件回放成功是两项不同结果；失败补丁和评分报告均保留。

| 指标 | 实际采集 | Rust 回放/校验 |
|---|---:|---|
| 高层 FUSE 回调 | 1,747,109 | 全部回放，包括预期错误 |
| read 回调 | 9,200 | 返回长度、内容 SHA256 一致 |
| write 回调 | 67,670 | 精确载荷及返回长度一致 |
| 实际读取字节 | 281,774,624（281.8 MB） | 一致 |
| 实际写入字节 | 194,706,661（194.7 MB） | 一致 |
| 重复读取字节 | 252,872,535 | 按采集操作计算 |
| 重复读取比例 | 89.74% | 不是缓存命中率 |
| 预期错误 | 45,525 | errno 一致 |
| 初始/最终文件树 | 已保存 | 路径、内容、链接目标、权限一致 |
| 会话数据库 | 1 个会话、26 条消息、109 个片段 | 文件摘要一致，SQLite quick_check=ok |

回调包含 1 次 init 和 1 次 destroy；其余为文件/目录操作。最多的是 getattr：1,588,057 次。预期错误为 ENOENT 21,596 次、ENODATA 22,881 次、ENOTTY 1,048 次，没有把预期错误算成回放失败。

## 仓库和状态分开看

| 范围 | 实际读 MB | 实际写 MB | 重复读取比例 |
|---|---:|---:|---:|
| 代码仓库 | 248.79 | 1.65 | 95.93% |
| HOME/XDG 状态 | 32.99 | 193.05 | 43.09% |

状态写入中，HOME 112.07 MB、config 55.26 MB、data 19.84 MB、cache 5.88 MB。HOME/config 包含运行时依赖安装和缓存；**193 MB 不能直接解释为对话历史增长或每次迁移必需传输量**。详细分项见 `metrics.json`。

重复读取按同一文件身份、同一偏移范围是否已读计算，覆盖写会使相应范围失效，rename/link 保留身份。这里是挂载点收到的字节，内核已命中的缓存不产生这些回调；不代表物理磁盘、云盘或网络流量。

## 完整性检查

开跑前实际执行了子进程、SQLite WAL、mmap、目录读取、rename、软/硬链接和 chmod 的预检，再由 Rust 回放，核对最终树。之后才启动模型；时间和挂载检查证据随数据保存。

捕获日志序号连续，并以正常 init/destroy 包围完整记录。Rust 遇到缺失结束记录、断号、缺失写入载荷或读内容不符会失败；6 项集成检查全部通过。实际任务还比较了最终硬链接关系，以及原始与回放后的会话数据库。

已经从下面的分卷重新组合、解压到另一个目录，再独立回放全部 1,747,109 条记录，初始树、最终树和读写总量再次一致。见 `portable-verification.json`。

## 数据格式与大小

完整压缩包 **127,774,151 字节（127.8 MB）**，分成 7 卷，每卷不超过 20 MiB，使用普通 Git，未使用 Git LFS。`bundle.json` 给出每卷和整体 SHA256。没有抽样、没有删减调用、没有用合成字节代替写入。

解压后主要内容：

- `initial/`、`initial-tree.json`：冻结的初始仓库和 Agent 状态。
- `capture/events.jsonl`：完整高层 FUSE 回调，原始约 684.2 MB；含顺序、参数、返回值、errno、PID、单调时间。
- `capture/blobs/`：66,958 个按 SHA256 保存的精确写入载荷，共约 193.3 MB；多次相同写入复用内容对象。
- `agent-events.jsonl`：完整 Agent 事件日志。
- `final-tree.json`、`verification.json`、`session-db-check.json`：最终状态与会话核验。
- `prediction.patch`、`grading/`：模型补丁和独立评分。
- `preflight/`、`tests.txt`、`provenance.json`：开跑检查、测试与版本/脚本摘要。

模型密钥仅在宿主转发器使用，Agent 环境中没有真实密钥。发布前扫描了 67,629 个文件中的已知凭据，通过，未做内容脱敏替换。

## 不花 token 的独立回放

在 Linux 上，从本实验仓库构建新增 Rust 入口，然后运行：

```bash
CARGO_TARGET_DIR=/home/hxy/ax-full-trace-build cargo build --release --locked \
  --manifest-path tools/full-fuse-replay/Cargo.toml

python3 scripts/replay_full_trace_bundle.py \
  --bundle results/opencode-full-fuse-pytest-10356-20261023 \
  --output /home/hxy/ax-full-trace/replay-new \
  --binary /home/hxy/ax-full-trace-build/release/full-fuse-replay
```

输出目录必须不存在。它检查分卷摘要、安全解压并保留普通权限，验证初始树，实际执行文件回放，再检查最终树和统计。不需要 OpenCode、模型凭据、Docker 或原始运行目录；建议预留数 GB 空间。

重新采集真实任务的方法见 [完整任务运行说明](../../scripts/full-fuse-trace.zh.md)，新增 Rust 格式及校验范围见 [回放器说明](../../tools/full-fuse-replay/README.md)。

## 范围与限制

采集覆盖任务整个生命周期中的 `/testbed` 和所有 `/agent-state` 状态。容器镜像中的运行库、网络与 `/tmp` 不在范围内，因此不称“整台机器全部系统调用”。这是 FUSE 高层回调，不是逐字节内核 FUSE 协议包；内核页缓存命中和本机锁处理不产生记录。

记录器单线程，目录/属性缓存超时为零，会影响原任务时间及元数据请求数。回放固定观察到的顺序，不重演原进程调度、锁竞争或模型决策；因此不是 Agent resume 验证。getattr 的设备/inode/所有者/时间及目录分配大小、statfs 动态容量没有要求跨机器相等。

这是一次完整任务的功能验收，不是 BrewFS 性能结果。Rust 用时包含 JSON 解析和内容检查，不能把它与含模型等待的 Agent 用时比较来宣称加速。任务失败也不会从记录中删除。
