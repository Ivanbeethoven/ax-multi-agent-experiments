# 两个文件验证 Packed lower 是否已热

在 WSL 的 `workspace-v1` FUSE 挂载上连续两遍读同一份 Packed lower（两个文件，77 B，禁止内联，FUSE 直读）。固定 BrewFS 提交 `ca07159f3da4b670e4aeb878faef928e69bc1634`，Redis 和 RustFS 都在本机运行。

| 每遍增量 | 首遍 | 第二遍 |
|---|---:|---:|
| FUSE 读操作 / 字节 | 2 / 77 B | 2 / 77 B |
| RustFS GET Range / 响应字节 | 10 / 27,271 B | 2 / 77 B |

两遍读出的内容都通过 SHA256 检查，第二遍仍向对象存储取回全部有效载荷。首遍对象字节还包含该遍触发的元数据读取。**当前配置未达到 lower 热态；没有启动 Overlay 开销计时。**

- [对象存储旁路回执](loopback.json)：在同一台 WSL 主机的 RustFS 入口统计实际 GET Range 和响应字节，只记录计数；测量结束后挂载、两个服务容器和临时目录都已清理。
- [原生指标回执](native-stats.json)：`.stats` 有 workspace 行缓存，但缺 Packed wire005 计数。该提交的 workspace 挂载先注册行缓存指标，再尝试注册 Packed 指标；`FsStats::set_extension` 只接受第一个扩展。因此缺字段不能当成零请求。

用具备 FUSE、Docker 和 boto3 的 WSL 环境，从实验仓库根目录运行 `python3 -B experiments/unified-suite/pilot-aliyun/evidence/preparation/c2-local-packed-warm-01/fixture.py NEW_RESULT.json`；加 `--native-stats` 仅检查原生指标。二进制、镜像及 fixture 输入固定为旁边回执中的版本。不占云机、不调用模型。

候选补丁（SHA256 `1f97b62ced4309f959738ef6cf2fdfbeacac0f33a8ec08bbd3dece7fbef6ebb6`）新增已校验的 wire005 缓存，默认关闭；尚未编译或合入上游。用候选产物复验时，添加 `--candidate-binary /absolute/path/brewfs --candidate-sha256 SHA256 --retained-bytes 1048576`。每次从后端填充时都校验帧；脚本核实际缓存上限和两遍各 2 次 FUSE 读、77 B，第二遍须有 Packed 缓存命中，且原生计数和 RustFS 入口均无后端读取。通过也只证明该挂载的 lower 能预热；同引擎 Overlay 开关和正式计时另行验证。

组合候选（SHA256 `1ca4440af71d3da2041ce2c7cf6a51378d11559cc6d31cd451423ccb702e7fa9`）还提供只读的数据区间对照，尚未编译或实测。添加 `--extent-pair` 会从同一个 workspace 和 Packed manifest 依次创建两次新挂载，分别检查预热，核 `ro` 和对写打开返回 `EROFS`。普通臂必须实际查 upper 的区间，对照臂必须实际跳过；两臂都必须仍走 FUSE，且通过上述零后端读检查。该对照只隔离空 upper 的数据区间查询，不能代表全部 Overlay、首次写入或完整任务开销。
