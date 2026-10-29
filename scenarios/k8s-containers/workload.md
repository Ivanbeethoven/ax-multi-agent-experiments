# 下载工具和负载，执行回放

## 1. 工具位置

实验仓库：`https://github.com/Ivanbeethoven/ax-multi-agent-experiments`，公开克隆；OSS原始数据已公开只读，支持匿名HTTPS下载。

**本批完整trace使用本仓库的 `tools/full-fuse-replay`**。独立仓库`fuse-workload-replay`的早期v1输入和27次操作样例不是本批格式。部署脚本位于本仓库的`scenarios/k8s-containers`；克隆仓库后，记录`git rev-parse HEAD`。不能访问GitHub的容器使用下面的代码包。

在当前仓库的Linux路径中打包：

```bash
tar -czf /tmp/ax-k8s-kit.tar.gz \
  scenarios/k8s-containers tools/full-fuse-replay \
  infra/rustfs/s3_probe.py workloads.json
sha256sum /tmp/ax-k8s-kit.tar.gz
```

通过平台文件入口传到容器，在`/opt/ax`解压。包不包含模型记录、大负载或凭据。

在与目标架构一致的Linux环境构建回放器：

```bash
cd /opt/ax
cargo build --release --locked \
  --manifest-path tools/full-fuse-replay/Cargo.toml --target-dir /tmp/ax-replay-build
mkdir -p /opt/ax-bin
cp /tmp/ax-replay-build/release/full-fuse-replay /opt/ax-bin/
sha256sum /opt/ax-bin/full-fuse-replay
```

本地已用Rust/Cargo 1.98.1构建。Lockfile v4需要支持该格式的Cargo；不要删除lock或改用另一组依赖。容器无编译器时提前编译，分发相同二进制并核对SHA256。

## 2. 负载位置

**最新清单为两批共7份**，见[数据位置、下载与回放](data-catalog.md)。下面保留原4份的执行示例；新增3份位于deepseek-opencode-20261026子目录，统一下载脚本见新清单。

公开只读的实验桶：`brewfs-agent-traces-20261023-4ccdae73`，杭州。

前缀：`oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023/`。

| 归档 | 压缩字节 | 回调数 | 实际读/写字节 |
|---|---:|---:|---:|
| django.tar.gz | 2,302,532,371 | 57,787,980 | 2,292,334,532 / 284,748,789 |
| sklearn.tar.gz | 842,598,913 | 9,233,326 | 1,753,942,573 / 305,043,902 |
| sympy.tar.gz | 590,808,335 | 7,265,338 | 1,124,648,770 / 232,052,204 |
| pytest-full-02.tar.gz | 407,606,955 | 1,747,109 | 281,774,624 / 194,706,661 |

文件大小、完整URI、HTTPS地址和SHA256已固定在 [archives.json](archives.json)，无需访问本机被Git忽略的runs目录。推荐使用[匿名HTTPS下载器](data-catalog.md)；下面保留ossutil 2示例，使用Anonymous模式，不使用RustFS账号下载OSS。

先下pytest：

```bash
export K=/opt/ax/scenarios/k8s-containers
export BASE=oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023
export OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
mkdir -p /input/archives /input/cases
aliyun ossutil cp "$BASE/pytest-full-02.tar.gz" /input/archives/pytest-full-02.tar.gz \
  --endpoint "$OSS_ENDPOINT" --region cn-hangzhou --mode Anonymous || exit 1
python3 "$K/scripts/check_archives.py" /input/archives --cases pytest-full-02 || exit 1
test ! -e /input/cases/pytest-full-02 || exit 1
tar --no-same-owner --same-permissions -xzf /input/archives/pytest-full-02.tar.gz -C /input/cases
```

确认成功后，同样下载另三份：

```bash
for name in django sklearn sympy; do
  aliyun ossutil cp "$BASE/$name.tar.gz" "/input/archives/$name.tar.gz" --endpoint "$OSS_ENDPOINT" --region cn-hangzhou --mode Anonymous || exit 1
  python3 "$K/scripts/check_archives.py" /input/archives --cases "$name" || exit 1
  test ! -e "/input/cases/$name" || exit 1
  tar --no-same-owner --same-permissions -xzf "/input/archives/$name.tar.gz" -C /input/cases || exit 1
done
```

仅阿里云杭州内网可达时可把endpoint换成`https://oss-cn-hangzhou-internal.aliyuncs.com`；其他平台使用公网地址。输入下载、解压、校验均在计时前完成，所有系统使用同一份输入存放方式。29 GB左右的JSON trace要顺序读，不将整份装入内存。

## 3. 解压后的格式

```text
pytest-full-02/
  initial/                 运行前的仓库和HOME/XDG状态
  initial-tree.json         初始树摘要、权限和链接清单
  capture/events.jsonl      每行一个FUSE回调：seq、op、参数、结果、errno、时间
  capture/blobs/<sha256>    实际写入内容，由events引用
  final-tree.json           最终内容与权限清单
  metrics.json              原始操作量、实际字节和重复读取比例
  backing/、replayed/       原采集最终状态与已验收回放副本
```

回放只需要initial、capture和清单；归档里的其他材料保留。所有输入只读使用，每项任务创建新的目标工作区。四个原始案例复制多份时仍是四种负载。

## 4. 在JuiceFS上运行完整pytest

先完成 [部署](deployment.md) 的挂载和监控。下面在w0执行：

```bash
export CAMPAIGN=jfs-pilot-01
export WORKER_ID=w0
mkdir -p /workspace/jfs/runs/$WORKER_ID /measure/$CAMPAIGN/tasks
python3 "$K/scripts/observe.py" probe --work-root /workspace/jfs/runs/$WORKER_ID \
  --out /measure/$CAMPAIGN/probe.json

python3 "$K/scripts/run_case.py" \
  --case /input/cases/pytest-full-02 \
  --workspace-parent /workspace/jfs/runs/$WORKER_ID \
  --out /measure/$CAMPAIGN/tasks/pytest-r1 \
  --binary /opt/ax-bin/full-fuse-replay --run-id pytest-r1 --worker-id "$WORKER_ID"

python3 "$K/scripts/summarize.py" /measure/$CAMPAIGN/tasks/pytest-r1/replay \
  > /measure/$CAMPAIGN/tasks/pytest-r1/replay-resources.json
```

查看`case-result.json`的valid、`replay.json`的operations/actual_read_bytes/actual_write_bytes，以及verify/stdout.log中的最终树核验。三阶段分别记录准备、回放和终检时间；任一步失败即停。重跑用新run-id和新输出目录。

确认四题均可执行后再按campaign计划扩大并发。失败的chown、errno或内容不能跳过；开始测试前先匹配UID/GID和能力。准备会复制并核验全树，因此本组标“已准备并核验”，不能称冷缓存。

Rust回放按每份trace原顺序执行，不调用模型、不启动原编译/测试子进程、不重放进程调度或文件锁。完整回放时间包含JSON解析、SHA256和路径检查；JuiceFS原生FUSE延迟另外从客户端指标取。

## 5. 保存本批结果

每个worker结束后执行一次同步补齐，然后停止本批任务和日志写入，再次同步；外部接收目录与源端文件大小/SHA256一致后才释放容器：

```bash
python3 "$K/scripts/artifacts.py" ship --once \
  --url "http://$MONITOR_IP:8088" --root /measure/$CAMPAIGN \
  --worker-id "$WORKER_ID" --token-file /secrets/obs.token
```

结果接收器默认只收任务记录和资源原始样本。服务日志、`.prom`快照、软件版本及配置摘要应在批末一并导出；用[指标文档](observability.md)的打包步骤保存，不能只保存图或Prometheus截图。
