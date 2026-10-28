# 三机 trace 入口

先执行 `python3 harness/run_trace_matrix.py plan --workers A B C --output runs/trace-plan.json`。
默认生成四轮顺序：每台机器各运行 native、overlay、Pack、JuiceFS 一次，共 12 格；各机按自己的顺序推进，不要求跨机同时开始。三台机器是独立配对区组，后端各自独立，不把该结果称作共享后端分布式扩展。

复制 `harness/trace-matrix.example.json` 为每机的私有配置，按计划填写 arms 顺序、唯一 work/output、同批资产摘要。worker 不编译；填入已核验的同一组二进制和镜像。新批次或额外默认组须使用新状态和空 Redis DB。

```bash
python3 harness/run_trace_matrix.py run --config worker-A.local.json --archive /var/log/ax/worker-A-results.tar.gz
```

默认四臂统一 TTL=0、KEEP_CACHE=false。可选 `*-default` 仅保留客户端 kernel TTL/KEEP_CACHE 默认；内存、磁盘缓存、压缩和持久化设置仍按统一规则控制，不代表全部 stock 配置。其结果须另列。

BrewFS 的真实 cache 字段按冻结源码核对：读/写内存各 512 MiB、reader/writer buffer 预算 1 GiB、读/写磁盘各 4 GiB、compression=none、writeback_mode=upload_before_commit。JuiceFS 1.4.2 实际 help 已核验 `--buffer-size=1024`、`--cache-size=8192` 为 MiB，显式启用 `--enable-xattr --enable-cap`，格式化 `--compress none`、不启用 writeback。两个完整客户端 job 用相同 cgroup 内存/CPU限制，并在挂载前核对内核实际限额。预算不是 RSS，其他缓存与实际 FUSE reply 缺测仍为 null。

回放前独立执行文件 fsync、正常卸载、启动新挂载进程并读回标记与原始树。该步骤在计时区间外，会预热缓存；不能据此报告全栈冷读。Pack 原始树必须来自预先构建的 lower，复制/打包上传发布成本单列；当前源是静止副本，未测生产工作区的固定版本接口成本。

保留全部原 trace/xattr，用原 Rust `--performance`，不设回放总时限；核对完成、操作数、读写字节与零句柄。性能模式不声称严格正确性。五秒观测和阶段前后 Redis/cgroup 数据随结果保存；指标未知时不填零。

结果包含每臂状态、准备/回放/卸载阶段、`terminal.json`。可选归档先扫描已知凭据，写逐文件摘要与代码摘要，再生成 tar.gz 和 SHA256 receipt。归档不上传、不释放资源；控制器必须另按本轮准确资源清单核验终态再清理。客户端 unit 状态未知时不生成可供清理的归档。普通 POSIX 预检回放与 mock 测试仅验证执行逻辑，不证明云端 FUSE/S3 持久性、有效 TTL、缓存预算或性能资格。