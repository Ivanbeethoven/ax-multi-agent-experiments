# FUSE 负载回放：现有 trace 审计

审计日期：2026-10-23。范围：现有采集文件、采集解析器、论文仓库的重算结果；没有运行模型、云资源或正式性能实验。

## 结论

现有八个 Agent 运行的日志可以确定操作种类、请求长度和时间分布，**不能直接转换成保持文件语义的完整回放**。新工具需要一种完整的输入格式，并另行采集可回放记录。若按旧日志的比例和大小生成新操作，应称“根据实测分布生成的负载”，不能称“原始 Agent trace 原样回放”。

通过挂载点调用 Linux 文件接口，可以让相同应用操作作用于 XFS、JuiceFS、CephFS、BrewFS 等挂载点。此时固定的是应用操作；内核缓存、预读和各文件系统配置仍会改变底层 FUSE 请求数。它不能保证逐条重现原始 FUSE 协议请求。

## 实际检查的文件

原始目录：

C:/Users/HXY/Documents/Codex/2026-10-03/wsl-localhost-ubuntu-24-04-home/outputs/agent-fuse-swebench/

- events.jsonl：557,135,223 字节，1,138,884 条 JSON 记录；本次完整流式读取并重新核对 SHA-256：4a9e1275439c36540202a980240e70751b23e95330628aed47dcbc1f26461544。
- collect_logfs.py：逐条阅读其 REQ、完成状态和参数解析逻辑。
- 当前仓库 experiments/agent-fuse-observations/README.md、analyze.py、derived/summary.json。
- 相邻 ../osdi2027-agent-fuse-observations-gemini/experiments/agent-fuse-observations/ 使用相同来源；不是第二份独立采集。

该导出目录没有 daemon 原始文本日志，但沿 runs.json 指向的实验目录，找到了 WSL 中八次运行的 fuse.log：/home/hxy/research/agent-fuse-swebench/agent-runs/*/fuse.log。此次只读该已知实验目录，未搜索用户其他目录或私有会话数据。

## 字段能回答什么

以下字段清单是对导出的 events.jsonl 完整扫描所得，并非抽样。原始 fuse.log 中可补回的字段见下一节。

| 需要的回放信息 | 现有记录 | 是否足够 |
|---|---|---|
| 操作类型 | 30 种 method；每任务 operation_id | 可统计类型和数量 |
| 请求开始/结束时间 | started_at、finished_at、relative_ms、latency_ms | 有壁钟时间与派生间隔；没有单独记录单调时钟 |
| 路径 | lookup/create 等有 parent inode + name；rename 有 old/new parent + name | 没有完整路径；lookup/create 成功返回的 inode 映射没有保存，不能可靠建立 inode→路径表 |
| 打开句柄 | read/write/flush/release 有 fh；open 只有 inode 和 flags | open/create 的返回 handle 没保存，不能可靠复原打开、使用、关闭的绑定 |
| 读请求 | 120,434 条；inode、fh、offset、size；response_bytes | 有长度和位置，没有返回内容或内容摘要 |
| 写请求 | 24,712 条；inode、fh、offset、data_len、write_flags、flags | 没有写入字节、内容对象引用或可复现生成规则 |
| rename | rename 5,845 条，rename2 1 条 | 参数存在，但依赖前述 inode 映射；rename2 的 flags 不能丢弃 |
| 线程和并发 | 顶层字段没有 PID、TID、stream、依赖边 | operation_id 和时间顺序不能代替线程先后关系或同步依赖 |
| 返回码 | status=ok/error/incomplete；读写有返回长度 | 253,669 条 error 没有 errno，不能都当作 ENOENT 或 ENOTSUP |
| 无正常回复的操作 | forget 12,688 条、destroy 8 条记录为 incomplete | 这是解析状态；不能直接算作 12,696 次文件操作失败 |
| 输入/输出文件树 | runs 有任务和代码版本信息 | 此 events 文件不包含初始文件树完整摘要和最终树摘要，不能单独验证重放等价 |

顶层字段只有：

task, agent, model, workload, operation_id, method, started_at, finished_at, latency_ms, args, request_bytes, response_bytes, status, relative_ms。

状态汇总与原重算结果一致：872,519 ok，253,669 error，12,696 incomplete。

collect_logfs.py 还存在两个采集边界：

1. PAIR 用逗号结束参数值，不能稳健保存含逗号的名字或结构化参数。
2. 找不到对应 REQ 的完成记录直接跳过；解析后的 JSONL 连续 ID 不能证明采集没有丢失完成记录。

## 原始文本日志能够补回的字段

本次也完整流式扫描了八份 fuse.log。原日志有 Request 的 unique、uid、gid、pid；ReplyEntry/ReplyCreated 的 inode；ReplyCreated/ReplyOpen 的 fh；以及 Error: Errno(n)。因此不能笼统声称“历史采集完全没有文件身份和返回码”：这些信息在导出的 JSONL 中丢失，可以编写新解析器补回。原日志的 pid 是 FUSE 请求中的身份字段，不能未经核对就当作完整的用户线程/同步记录。

全部 24,712 次 write 在原日志中各有三类记录：带 data_len 的请求、Wrote n bytes、ReplyWrite written。没有第四类 write 记录；这些记录仍未保存写入字节。找回 inode、fh、errno 不能弥补这一缺口。即使最终工作区仍存在，也不能从最终文件恢复已覆盖或已删除的中间写入。

| 原始运行目录 | fuse.log 字节数 | SHA-256 |
|---|---:|---|
| 01-astropy__astropy-13398-dsh-exploratory | 286,436,556 | dbb8e5c8a7b9000f369413d034c7bd3603e553582d0b2ca80f74d191347dce21 |
| 01-django__django-10097-dsh | 55,169,649 | 933174f1119b9df5d4364ffccd8118e98afd0cd78a05bf3955c24c9f682caa8e |
| 02-matplotlib__matplotlib-13989-dsh | 22,200,673 | cb17a05406cc7790d7447b4a3d295ddd19669ddf0d2ca7e9d68894628ffddc68 |
| 02-pytest-dev__pytest-5787-dsh-exploratory | 25,583,999 | a702de2ddd8b16b6e421b22d62143702e35b9e0df9bc245dee4320ca9f75b33c |
| 03-pallets__flask-5014-dsh | 3,624,301 | dad598e80cad57410a47a520969d2d076055d15ae27890d9e108866752d87a4a |
| 03-sympy__sympy-12489-dsh-exploratory | 105,310,357 | f8930a7fb12f470f5566ce6274347caaae532f993a4afad302906ecaa0acd9d4 |
| 04-psf__requests-1142-dsh | 3,734,242 | 5332775b9b5986ecaf41fae0fb14da92f9ad6e1ea37739fc03c8888b00a20f7b |
| 05-sphinx-doc__sphinx-10323-dsh | 12,480,657 | d1745133f4e15eafad4538254eb96445df3dbf11ba154b87503f42451679a2c1 |

建议保留两条来源路径：旧 JSONL 继续支持已有论文统计；新解析器从上述 fuse.log 恢复身份和 errno，若用合成字节填补 write，必须在新 trace 中明确标记 synthetic/mixed。完整字节回放仍需新采集。

## 新采集格式的最低要求

1. **输入树和环境**：固定仓库 commit、文件树内容摘要、权限/链接规则、工具版本、内核和挂载选项。输入树必须与 trace 配套。
2. **文件身份**：相对工作区路径与逻辑句柄；open/create 结果绑定句柄；rename/unlink 之后仍打开的文件必须依照句柄继续操作。不能每次 read/write 都按旧路径重新打开。
3. **写入内容**：保存确切字节或不可变内容文件的 offset/length/hash。种子生成只适合明确标记的合成负载；不能用随机字节替换真实 trace 的写入而仍称其等价。
4. **返回结果**：保存期望返回长度、errno；必要时保存读取内容摘要。错误探测属于负载，不能只留下成功调用。
5. **顺序与并发**：保存每个流的顺序、单调开始时间、线程/任务标识和显式跨流依赖；共享句柄、rename/read、生产后消费等需要依赖边。未支持的操作必须拒绝或明确列入排除，不静默近似。
6. **验收**：回放后校验重要输出文件和最终工作区摘要。移机实验另外核对 Agent 会话状态和下一步工具执行；文件回放成功不能独立证明模型会话恢复成功。

建议同时保留两个回放模式：

- **按固定时间提交**：使用固定的外部到达时间，单独记录提交延迟和排队时间；前序依赖尚未完成时不能越过。
- **去掉外部等待**：只去掉已明确标注的模型等待/任务空闲；保留操作依赖，测存储可达吞吐。不能把原存储响应时间再睡一次，否则会把旧系统性能写进新实验。

在缺少工具阶段边界的旧采集中，不能可靠区分模型等待与等待文件操作完成。

## 对 Rust 实现的审查要求

- 将配置解析、trace 解析/验证、写入数据准备放在计时段之外；预先分配每工作线程缓冲，避免每次 I/O 解析 JSON、写一行日志或争用全局统计锁。
- 单独记录 trace 加载时间、内存占用和测量期开销；采用每线程计数与有界直方图，不用每操作堆分配的大数组造成采样器瓶颈。
- 路径必须限制在显式工作区内。绝对路径、..、NUL、父目录符号链接，以及运行期间链接替换，不能逃出工作区。只做字符串前缀检查不足以防止逃逸。
- 正确处理短读、短写、EINTR、EOF、errno 和 close；不能把部分写入的字节数统计成请求字节数。
- 性能模式可以不计算每个 read 的内容 hash，但必须有同一 trace 的严格正确性检查路径，且报告使用了哪一种模式。
- 多流调度必须保持依赖；单个流失败时不能让其他流永远等待。取消和超时必须结束等待者并将失败计入最终结果。
- 不能将 flush 自动等同 fsync，或将 FUSE forget 自动翻译为文件删除；接口层没有对应语义的操作应明确拒绝。
- “高性能”需要独立测量工具本身的 CPU、RSS、调度误差及无 I/O 上限；实现采用 Rust 本身不证明回放器没有成为瓶颈。

## 当前准备状态

历史数据的完整字段审计已经完成。新工具正在 tools/fuse-replay/ 实现；具体支持范围和本地测试结果以工具 README、测试报告及后续代码审查为准。没有从历史日志制造可回放 payload，也没有将合成测试当作正式 Agent 测量。

## 首版 Rust 执行器的独立检查

只读审查了 trace.rs、safe.rs、replay.rs、main.rs 和 integration tests。以下问题已向实现者指出并在后续代码中确认修复：

- paced 模式在多个流共用一个 worker 时会因文件记录顺序产生额外等待。当前明确要求 paced 模式至少每个流一个 worker，并报告提交迟到时间。
- 未指定 expected_bytes 时，短读写原先可能被算作正确。当前成功读写默认必须返回请求长度；EOF 或预期短返回需要明确写入 expected_bytes。
- 原先执行可无限等待。当前执行期限到达后退出 124，输出 valid=false，未知的部分计数保留 null。期限针对 execution；准备阶段仍需外部总体超时保护。
- FD 数组原先每个 worker 复制所有流的槽位。当前只为该 worker 负责的流分配实际槽位。
- 失败 close 的 FD 生命周期不能可靠按成功 close 以外的简单规则描述。当前格式拒绝 expected-error close。
- 路径保护不能单独防止预置的跨目录硬链接。当前在执行前检查整个初始树，拒绝硬链接文件和特殊文件。此扫描会预热元数据；不能把这样的运行直接称作冷缓存实验。

独立检查使用 WSL 中 /tmp/codex-fuse-replay-target/debug/fuse-replay，SHA-256 为 1f93ad8ae83f851210f281ee242bbb9b8fddab5cdb1ac36d2b092a3511ee034d。所有数据位于自动清理的专用临时目录，没有使用真实 Agent 或云挂载。

| 独立场景 | 结果 |
|---|---|
| open 后 rename、unlink，再通过原 FD 写入和读取，并核对确切 6 字节内容 | 通过；8 个操作，结束后无文件名和未关闭 FD |
| 通过根内父目录符号链接尝试 unlink 根外文件 | 通过；根外内容不变，只有 1 次失败的安全 open，无目标 unlink syscall |
| 两个流按 150 ms、0 ms 的不同时间到达，分别运行在两个 worker | 通过；先到流没有被另一流的等待拖延，最大提交迟到约 0.124 ms |

以上结果验证执行器语义和调度边界，**不是文件系统性能提升证据**。工具本身通过 Linux 文件接口运行，安全 open/close 的额外调用单独计数；它不会逐条注入 FUSE 协议包，也不补造历史 write 内容。

## 最终候选版本验收

在实现者补充只读重复回放功能后，独立重跑 cargo test --locked --quiet：19 项集成测试通过，0 失败。只读重复的允许集合、open 标志、每轮 phase 屏障、FD 关闭和计数均已审查；写入、create/truncate、fsync 及 paced 重复被拒绝。8 遍同进程回放是一个测量单元，不能当作 8 个独立重复。

另用独立校验脚本核对 tools/fuse-replay/validation/2026-10-23-local-ext4 中全部 36 份运行结果及 12 组汇总，以下检查全部通过：

- 每格 3 次独立进程运行，全部 valid=true、退出码 0、mismatches=0。
- trace SHA256 与 environment.json 一致，所有配置统一 iterations=8。
- stat 每进程 8,388,608 个操作；主调用、安全 open 和安全 close 分别为相同数量。
- pread 每进程 8,388,608 次读取及 256 次打开/关闭，共 8,388,864 个操作；读取 34,359,738,368 字节。
- 每个吞吐值等于实际总操作数除以 execution_seconds。
- 12 组 min、median、max 均可由逐次记录重算得到。

README 和 validation/README.md 明确这些是暖缓存本地 ext4 上的回放器测量，保留准备成本、短测限制和 4→16 worker 的 stat 吞吐不再提升的结果。没有把热内存复制速率写成磁盘或网络带宽。代码与文档审核未保留已知阻断问题；已告知实现者可按用户授权将工具作为独立仓库推送。本审计文件不属于该独立工具仓库。

