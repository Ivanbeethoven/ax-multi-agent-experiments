# 已有 SSH 节点：实验 Agent 操作入口

适用于配置好 SSH 的 Linux 容器、虚拟机和服务器。控制端使用 Python 3.11+ 和 OpenSSH；节点运行同一份实验代码与已固定版本的工具。当前任务是准备脚本，实验启动另行交接。

实验设置见 [C1–C4 总览](../PLAN.zh.md)。节点数和资源配额来自实际交付清单；阿里云 CLI、Kubernetes 和 Docker 管理权限均不是直接 SSH 模式的依赖。

## 1. 分配运行环境

| 接入方式 | 一个 Agent 如何隔离 | peer 设置 |
|---|---|---|
| SSH 直接进入容器 | 使用该容器现有的挂载空间和 cgroup | container/cgroup 留空 |
| SSH 到宿主，再进已有 Docker | 每个 Agent 一个已有容器 | container=ax-实际名称 |
| SSH 到 Linux/VM | 一个独立 cgroup；管理员提前配置 | cgroup=/sys/fs/cgroup/ax-批次/agent |

直接 SSH 的多个容器可以共用地址、使用不同端口；每个入口配置独立 SSH alias。`environment_id` 填真实容器或 VM 身份，`host_id` 标识共用资源的宿主组。C4 跨物理机结论另需物理宿主证据。

C1 每 Agent 的 FUSE 客户端和回放器合计 2 CPU / 8 GiB、swap=0。容器需提供可访问的 `/dev/fuse`、可用的挂载权限及 cgroup v2。`probe` 检查可见能力，实际挂载和配额在资格步骤核验。

宿主观测入口运行在 Agent 配额之外。多个容器共享一台宿主时只需一个宿主采集器。只有容器 SSH、没有宿主观测入口时，可以准备配置和验证功能；完整性能验收需要补齐宿主指标。

## 2. 冻结代码和工作目录

控制端与节点使用相同提交。构建一次，按 SHA 分发；源码与 RustFS 分支规则见根目录 [AGENTS](../../../AGENTS.md)。SSH 配置、私钥和凭据留在受控环境，实验代码不安装 SSH 服务或修改服务器配置。

推荐每个批次集中保存：

```text
runs/<campaign>/
  plan.json                 实验条件、顺序和区组
  fleet.json                实际节点、角色、槽位和宿主关系
  pins/                     代码、二进制、镜像和输入摘要
  probes/<node>/<slot>/      环境探测与 peer.json
  qualification/            内容、资源、网络和观测验收
  specs/<cell>/<agent>/      挂载、任务、observer、cohort 配置
  results/<cell>/<attempt>/  原始结果、日志、采样和验收
  manifest.json             保全清单
```

每条命令的输出使用新路径。控制端与节点可以采用不同目录，所有 spec 使用所在机器的绝对路径。

## 3. 生成方法矩阵和节点清单〔控制端〕

首次 C1 用完整 pytest-full-02，N=1/8，两种文件系统各一次，共4个流程批次：

```bash
python3 bench/scale_plan.py --campaign ecs-20261101 --cluster C1 \
  --profile pilot-8 --provider ssh --isolation docker --output "$RUN/plan.json"
```

`ecs-20261101` 是方法版本标识；provider=ssh 输出工作量矩阵，机器预算留给实际清单。C1 正式范围用 c1-8；C2–C4 用 main，按批准条件执行。

以下示例接入8个 Agent 容器、一个服务入口、两个宿主观测入口；数量按实际交付修改：

```bash
python3 -m bench.runtime.fleet template --cluster C1 \
  --executors a01 a02 a03 a04 a05 a06 a07 a08 \
  --services backend --hosts host01 host02 --output "$RUN/fleet.json"
```

填写 campaign_id、真实 environment_id/host_id、host_observer_node、代码/Python/工作路径、host-key 验证结果和 data_paths 的容量/inode 下限。slots 记录实际 Agent 放置；同一宿主通过 Docker exec 或独立 cgroup 承载多个槽位。服务角色可共置 Redis/RustFS，但资源预算和共机关系要冻结，两臂一致。

```bash
python3 -m bench.runtime.fleet check --inventory "$RUN/fleet.json" --output "$RUN/fleet-check.json"
```

## 4. 检查一个 SSH 入口〔控制端〕

使用独立、自包含的 SSH 配置。端口、用户、密钥与 ProxyJump 均沿用实际交付值；目标和跳板都设置下列选项：

```sshconfig
Host a01
  HostName 127.0.0.1
  Port 2201
  User experiment
  IdentityFile /absolute/private/key
  IdentitiesOnly yes
  StrictHostKeyChecking yes
  UserKnownHostsFile /absolute/private/known_hosts
  GlobalKnownHostsFile none
  ForwardAgent no
  PermitLocalCommand no
```

known_hosts 从已验证的节点身份建立。配置保持独立 Host 块，传输器会核目标及跳板；Include、Match 和自定义 ProxyCommand 改写为这份配置内的明确 Host/ProxyJump。

```bash
python3 -m bench.runtime.fleet probe --inventory "$RUN/fleet.json" --node a01 --slot a01 \
  --ssh-config "$SSH_CONFIG" --known-hosts "$KNOWN_HOSTS" --output "$RUN/probes/a01"
```

对服务和独立宿主观测节点省略 --slot。若同一个 host 节点既承载 Agent 槽位又收集宿主指标，另用 `--host-observer`、省略 --slot 生成宿主 peer，输出到独立目录；这条连接运行在 Agent 配额之外。

probe 只读核验 Python、runtime 文件摘要、FUSE 可见能力、cgroup、工作目录和容量，输出 probe.json；源码一致时生成 peer.json。peer 是后续控制命令的连接描述，probe 的 pending 列出剩余资格。镜像/二进制、共享服务连接、实际挂载、配额与宿主总容量分别验收。

## 5. 准备挂载和任务〔执行节点〕

按 [数据交接](DATA.zh.md) 核完整归档与文件清单，导入相同初态。用 [prepare mount](ADAPTERS.zh.md#3-原生挂载) 生成挂载配置；原生服务、FUSE、内容、errno 和重新挂载检查通过后准备任务。

[trace-request.example.json](../../../bench/runtime/examples/trace-request.example.json) 是单 Agent 输入模板。填写已核验的 case、文件清单、回放器、目标目录和 mount 回执：

```bash
python3 -m bench.runtime.prepare trace-worker --request "$TRACE_REQUEST" --output "$NEW_JOB_DIR"
```

生成 step.json、worker.json，完整操作数/字节数从 metrics.json 读取；默认 performance 回放、LLM 等待关闭。先审阅 step 并更新 worker 中的 step SHA，再审阅 worker。初态及全部输入在计时前验证；结果与输入均放在被测挂载外。

控制端通过 peer 调用一个挂载操作：

```bash
python3 -m bench.runtime.fleet call --peer "$PEER" --sha256 "$PEER_SHA" \
  --operation start --spec "$REMOTE_MOUNT_SPEC" --spec-sha256 "$MOUNT_SHA" \
  --state "$REMOTE_MOUNT_STATE" --output "$RUN/mount-start-a01"
```

status/stop 使用同一 peer 和 state；stop 等待原客户端正常排空并退出。命令不会创建、销毁容器或云实例。

## 6. 准备观测与一个批次〔控制端〕

按 [观测协议](OBSERVABILITY.zh.md) 固定原生指标和开销资格。每个节点分别 observe-start；同一宿主的宿主指标由映射的 host observer 收集。

[cohort-request.example.json](../../../bench/runtime/examples/cohort-request.example.json) 给出任务、peer 引用和资格文件结构。每个 worker 填 id、node_id、slot_id、peer_ref、远端 worker spec 和新 output；observer 填对应 peer_ref 和状态路径，并覆盖本组清单中的全部节点。执行宿主的 observer 使用上述宿主 peer；直接 SSH 容器的客户端指标使用容器 peer。

```bash
python3 -m bench.runtime.fleet cohort --request "$COHORT_REQUEST" --output "$RUN/cohort.json"
```

审阅后固定 cohort SHA。运行入口一次释放一个批次，所有 worker 完成预检后统一开始：

```bash
python3 -m bench.runtime.cohort --spec "$RUN/cohort.json" --sha256 "$COHORT_SHA" --output "$COHORT_RESULT"
```

C2 的 worker 调用 ml-corpus-scan，C3 调用 s3_requests，C4 派生调用原生命令；都复用同一 SSH、屏障和采集流程。C4 流转使用 [handoff](ADAPTERS.zh.md#5-一次-c4-交接)。

## 7. 完成验收和保全

顺序记录应用完成、drain、独立客户端读回；随后停止 observer，执行 audit，下载本批结果和原始采样，再用 experiment_step seal/verify 核清单。脚本和回执详见 [runtime 索引](../../../bench/runtime/README.md)。

执行 Agent 在步骤之间检查输出，计时期间由脚本完成调度。连接中断后先核原进程和原输出；失败、慢值与缺测一并保留。准备阶段的检查记录、performance 回放和严格内容验证分别报告。
