# 阿里云实验入口

**先跑单机诊断。正式五个 RQ 仍需资格验证，不能把脚本能运行等同于论文实验完成。** 不创建机器、不格式化盘、不自动释放资源。原 Terraform 和 [RustFS 部署](deploy/README.md) 继续负责装置。

## 运行

1. 选择 `single-node-four-disk` 部署模式可生成单机四盘配置；正式四节点仍用 `distributed`。用 `deploy/freeze_backend.py --help` 冻结指定 RustFS 分支、完整 SHA、镜像 digest、BrewFS SHA/二进制摘要。保留镜像构建记录。运行前还会核验实际容器身份与本机 S3 端口。
2. 在已装好 Cargo、Docker、fio、redis-cli、FUSE 的 Linux 实例上构建。构建和数据放数据盘，系统盘仅放低频日志。按磁盘 ID 核对挂载，不由脚本猜盘或格式化。

```bash
AX_BUILD_ROOT=/mnt/client-data/build-new bash infra/alicloud/prepare-worker.sh
python3 infra/alicloud/calibrate.py --disk /mnt/rustfs0 --disk /mnt/rustfs1 \
  --disk /mnt/rustfs2 --disk /mnt/rustfs3 --output /var/log/ax/calibration-new
```

标定包含真实预填充/校验、逐盘及并发的顺序读/写、随机读。另登记实例级 IOPS/带宽上限、磁盘大小/PL，不以单盘吞吐上限判实验无效。

3. 复制 `suite.example.json` 为私有 `*.local.json`，填真实摘要和新目录。每配置使用专用桶和空 Redis DB，凭据通过受保护环境中的 `AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY` 提供；Redis 密码用 `REDISCLI_AUTH`，含密码 URL 会拒绝。BrewFS 的 Redis认证配置需另确认，不能仅凭 redis-cli 登录成功推断挂载支持。

```bash
python3 harness/aliyun_suite.py diagnostics --spec native.local.json --output /var/log/ax/native-new
```

只支持本机 RustFS 的诊断装置。每次真实卸载并重新启动 FUSE，使用相同、已写入并核验的语料；暖态实际遍历读入。TTL 同时设置 CLI 和 legacy 环境变量，KEEP_CACHE 用源码支持的环境变量。记录请求设置，**实际 FUSE reply 值缺测时仍为 null**。冷态默认仅指新客户端；远端缓存未控制，不称全栈冷读。JuiceFS 不开启 writeback，KEEP_CACHE 专属对照记 unsupported。

## 其它已接入口

| `aliyun_suite.py` 模式 | 输入补充与边界 |
|---|---|
| `packed-build` | `fixture` 路径/摘要、`source`、S3 bucket/endpoint。producer 要求真实只读 Btrfs snapshot，并自行核验；记录打包+上传+发布总墙钟。此前创建固定版本的成本单列，不能声称已测 DB 工作区封存。 |
| `packed-queries` | `manifest_key`（Pack）或 `parent_revision`（native overlay），相同 Rust corpus 路径、files/file_bytes，consumers、query=`stat/read/readdir`、operations、warm、neighbor_operations。可加 consumer_interval_ns、neighbor_interval_ns、neighbor_slo_ns，按固定到达表测邻居延迟/按时完成率；过载请求排队后全部执行，不因 SLO 超期杀掉。默认 interval=0 为闭环，持续负载速率仍须预标定。整批测 Redis 差值。 |
| `object-io` | files/file_bytes/operations，client_block_bytes；可选 ec_block_hint。实跑顺序读、4 KiB 稀疏读、覆盖写；真实 B/q 和 HTTP/设备层计数缺测为 null，不因 hint 存在就称 dynamic 生效。 |
| `fork-base` | native-overlay、已有 parent_revision、fork_count、file_bytes。真实 CLI fork、子挂载正确读、每子 1 MiB 私有写和兄弟隔离检查；尚非“修改父状态后封存”的 F1。 |
| `trace` | replayer 路径/摘要、trace_case、trace_manifest、trace_manifest_sha256。manifest 为相对文件名→SHA256，覆盖 events、blob、initial、initial-tree、metrics。先校验并 `cp -a` 初始状态，再调用原 Rust `--performance`，保留 xattr；核对完成、操作/字节数和零句柄。不设总超时，不宣称严格正确性。 |

所有模式正常卸载；失败保留 result.json、phases.json、原始日志和数据，不清桶/删云盘。观测默认每 5 秒读一次 `/proc`，Redis 在阶段边界读 INFO；记录的是整台 Redis（含观测请求），不能当单客户端精确计数。`observe=false` 可配对测量周期观测开销；边界 INFO 仍保留。

## 任务与分析

```bash
python3 harness/final_plan.py --output runs/final-main.json
python3 harness/packed_cost.py costs.local.json costs-result.json
python3 harness/summarize_suite.py /var/log/ax/native-new /var/log/ax/overlay-new --output diagnostic-summary.json
```

任务表覆盖五个 RQ，三独立配对批次、固定随机种子，明确每格所需能力；不会自动开跑。成本输入单位只能是可加资源，native/pack 各提供 F 和等长的 Q/U/G 数组；U/G 是每次增量，不重复计构建。墙钟并发只报直接测量的 makespan。

诊断汇总按输入与控制设置分组，保留原值、中位数、范围和所有批次状态；失败批次不填零。trace 详细计数保留在原格式 replay.json，消费者/邻居各保留 Rust JSON，不能与单机诊断混为一次总体胜负。

跨主机派发用 `python3 infra/alicloud/dispatch.py jobs.local.json --output runs/dispatch-new` 先检查；加 `--execute` 才运行。清单含 concurrency 和 jobs；每 job 提供 id、host、repository、spec、output、work_root、environment_file、redis_reservation（实际 Redis URL）、mode。先在各机准备匹配的私有 spec 和 0600 环境文件；不会上传凭据。systemd 不设运行时限，SSH 丢失记 unknown，不重投；恢复后使用 `--collect-only` 和新输出目录收集。控制器耗时包含建挂载与传输，不冒充纯回放 makespan；完成后检查并停止本批 `ax-<id>` 保留单元。

**还缺的系统能力/实机资格：**修改后父工作区的 seal/save 接口及对应成本、跨物理节点交接/fencing、绑定对象的 B/q/分层计数、正式分布式存储拓扑上的 56-task 批次和真实 Agent session API。派发器已实现，但 worker 目前只接受本机 RustFS 诊断装置，不能把多台各自存储的诊断当成共享存储主实验。当前检查的 BrewFS `2457de5` CLI 没有 seal 命令，`workspace migrate` 是格式迁移，不能冒充工作区搬迁。这些格保持 not_run/unsupported；准备好接口后再放行对应 RQ，不阻断独立诊断。
