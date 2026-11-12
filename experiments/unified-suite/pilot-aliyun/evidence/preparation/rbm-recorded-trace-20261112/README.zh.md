# rbm：真实 FUSE 小负载回放入口

BrewFS 和 JuiceFS 均通过：同一份 **367 次回调，读 82,192 字节、写 107,307 字节**；卸载后用新缓存重挂，文件内容、权限、硬链接和 SQLite 校验一致。重复读取比例为 14.94%。见 [结果](summary.json)。测试服务、挂载及临时目录已清理。

输入由 [采集脚本](../../../scripts/capture-smoke-trace.py)实际记录文件覆盖、链接和 SQLite 操作；无模型调用，不是 OpenCode 任务或会话恢复。 [input.tar.gz](input.tar.gz)含初始树、全部 events/blobs 和校验清单，可解压后直接回放。rbm 原输入为 `/var/tmp/ax-recorded-smoke-20261112-02`。

入口新增 `--full-trace --trace-timeout 90`，不再限制为 140 回调；按输入 metrics 检查操作数、读写量、句柄、完整树、链接和数据库。使用当前 Rust 回放器的 `--portable-terminal-probe --cache-parent-fds`。JuiceFS 终端探针无差异；BrewFS 保留 4 次 TCGETS ENOTTY→EOPNOTSUPP 兼容记录，读字节均严格检查。不是正式性能数据。

已修复 JuiceFS 挂载缺 `--enable-xattr --enable-cap` 导致 security.capability 返回错误的问题；[首轮失败](juicefs-first-failure/receipt.json)原样保留。输入复制改为 `cp -a` 保留硬链接。树检查只跳过 JuiceFS 根控制文件，子目录同名用户文件仍校验。

第一次采集因比较 FUSE 提供的 inode 编号失败，原目录保留在 `/var/tmp/ax-recorded-smoke-20261112-01`；改为检查 backing 的硬链接身份、挂载内检查别名读回，再采集成功输入。未修改旧采集。

构建：BrewFS main `ca07159f3da4b670e4aeb878faef928e69bc1634`，RustFS 指定分支 `f4ed202508f2153afb7956bae6f7799b60eccd00`；二进制及镜像摘要见各 receipt。两条远端分支运行前已核对。此次不新开云机。

复用 [最小验证命令](../../../RBM-MINIMAL.zh.md)，选择 `C2-replay`、`juicefs` 或 `brewfs-full`，增加 `--full-trace --trace-timeout 90 --trace /path/to/extracted/input --replay /path/to/current/full-fuse-replay`。实际完整参数保存在各 receipt 的 argv。真正的完整 Agent trace 使用相同入口和原数据 metrics，不使用这份小负载的操作数。

验证：`test_trace_controls.py` 2 项、`test_pilot_trace_readback.py` 5 项、`test_experiment_smoke.py` 6 项通过。前两组设置 `FULL_FUSE_REPLAY_BIN=/var/tmp/ax-full-replay-20261112/release/full-fuse-replay`，使用实际 Rust 回放；覆盖 FIFO 不阻塞、同名用户文件损坏仍拒绝、冷读损坏和工作量错误拒绝。
