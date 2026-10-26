# 部署顺序和启动命令

首轮5个容器见 [容器清单](README.md)。下面命令均在已分配容器内运行普通进程，不使用kubectl、Docker或systemd。路径约定：代码在`/opt/ax`，二进制在`/opt/ax-bin`，`/data`为平台分配的持久目录，`/measure`为测量目录，`/input`为输入目录；无写权限时统一换成平台给出的路径。

正式4—128客户端组按[规模表](scale.md)扩大服务端资源，将redis.conf复制到/data/redis/redis.conf并把maxmemory改为24gb，设置`export REDIS_CONFIG=/data/redis/redis.conf`；下面默认2gb只用于起步组。hosts.local.json列出该批全部worker，配置生成器按列表生成抓取目标。

## 1. 软件、地址和目录

| 软件 | 本轮固定版本/来源 | 所在容器 |
|---|---|---|
| JuiceFS CE | [1.4.2发布包](https://github.com/juicedata/juicefs/releases/tag/v1.4.2)，Linux amd64/arm64按实际架构选 | worker |
| RustFS | [1.0.3发布包](https://github.com/rustfs/rustfs/releases/tag/1.0.3)，选带v1.0.3的zip并核对SHA256SUMS | s0 |
| Redis | [7.2.16源码](https://download.redis.io/releases/redis-7.2.16.tar.gz)；可在相同Linux环境执行`make -j2 MALLOC=libc`后拷贝src/redis-server和redis-cli | redis；worker需要redis-cli |
| Prometheus/promtool | [3.15.1发布包](https://github.com/prometheus/prometheus/releases/tag/v3.15.1) | monitor |
| OTel Collector | [otelcol-contrib 0.163.2](https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v0.163.2)，必须是contrib版，包含Prometheus exporter | monitor |
| redis_exporter | [1.96.0发布包](https://github.com/oliver006/redis_exporter/releases/tag/v1.96.0) | redis |
| Python/Rust工具 | Python≥3.10；回放器构建见[负载使用](workload.md) | 全部/Python；worker/Rust回放 |
| 传输工具 | 输入使用Python标准库匿名HTTPS下载；上传需已授权Aliyun CLI；S3建桶使用AWS CLI或Python boto3 | 下载入口；worker |

不能联网的容器提前拷贝二进制。每个角色保存`--version`与`sha256sum`，跨批次不换版本。RustFS、Redis、JuiceFS和监控均只绑定分配的私网IP；以下明文HTTP用于隔离的实验私网。

复制`deploy/hosts.example.json`为`hosts.local.json`，将所有示例IP替换成实际地址。示例要求每个角色独立IP；若平台把多个容器放在同一Pod共享IP，先合并计量范围、改端口表再生成，不能直接套用。

所有容器设置非密钥变量：

```bash
export PATH=/opt/ax-bin:$PATH
export AX=/opt/ax
export K=$AX/scenarios/k8s-containers
export CAMPAIGN=jfs-pilot-01
export MONITOR_IP=实际监控IP
export REDIS_IP=实际RedisIP
export S3_IP=实际RustFSIP
export SELF_IP=本容器IP
export WORKER_ID=w0  # 各容器分别为w0、w1、redis、s0、monitor
mkdir -p /measure/$CAMPAIGN
```

需要平台注入的文件：`/secrets/redis.password`（密码本身）、`redis-auth.conf`（`requirepass "同一密码"`）、`rustfs.env`（RUSTFS_ACCESS_KEY/RUSTFS_SECRET_KEY）、`s3-client.env`（同一实验S3账号的ACCESS_KEY/SECRET_KEY）、`obs.token`（至少32字符随机令牌）。权限0600，不放入输入、结果或Git。OSS输入无需凭据；结果上传身份与实验RustFS凭据分别配置。

| 来源 → 目标 | 端口 | 用途 |
|---|---:|---|
| worker → redis | 6379 | JuiceFS元数据 |
| worker → s0 | 9000 | S3数据 |
| monitor → worker | 9567 | JuiceFS指标 |
| monitor → 所有容器 | 9108 | 自己cgroup/网络命名空间指标 |
| monitor → redis | 9121 | Redis指标 |
| s0 → monitor | 4318 | RustFS OTLP指标 |
| 所有容器 → monitor | 8088 | 原始观测文件增量传输 |
| monitor本机 | 8889、9090 | OTel到Prometheus、Prometheus查询 |

## 2. 先启动monitor

```bash
python3 "$K/deploy/render.py" hosts.local.json /data/monitor/config-$CAMPAIGN
promtool check config /data/monitor/config-$CAMPAIGN/prometheus.yml
otelcol-contrib validate --config=/data/monitor/config-$CAMPAIGN/otel.yml
mkdir -p /data/monitor/tsdb-$CAMPAIGN /data/monitor/results-$CAMPAIGN

nohup otelcol-contrib --config=/data/monitor/config-$CAMPAIGN/otel.yml \
  > /measure/$CAMPAIGN/otel.log 2>&1 &
nohup prometheus --config.file=/data/monitor/config-$CAMPAIGN/prometheus.yml \
  --storage.tsdb.path=/data/monitor/tsdb-$CAMPAIGN --storage.tsdb.retention.time=7d \
  --web.listen-address=127.0.0.1:9090 --web.enable-admin-api \
  > /measure/$CAMPAIGN/prometheus.log 2>&1 &
nohup python3 "$K/scripts/artifacts.py" serve --bind "$MONITOR_IP" \
  --root /data/monitor/results-$CAMPAIGN --token-file /secrets/obs.token \
  > /measure/$CAMPAIGN/artifact-server.log 2>&1 &
```

这里不部署Grafana/Loki。Prometheus保存时序指标，结果接收器保存原始JSON/日志。RustFS用OTLP发送给Collector，再由Prometheus抓`127.0.0.1:8889`；不能抓RustFS的`9000/metrics`。[RustFS监控接口](https://docs.rustfs.com/en/operations/observability)

## 3. 启动Redis

在redis容器：

```bash
mkdir -p /data/redis
nohup redis-server "${REDIS_CONFIG:-$K/deploy/redis.conf}" --bind 127.0.0.1 "$REDIS_IP" \
  > /measure/$CAMPAIGN/redis.log 2>&1 &
export REDISCLI_AUTH="$(cat /secrets/redis.password)"
redis-cli -h 127.0.0.1 PING
redis-cli -h 127.0.0.1 CONFIG GET appendonly appendfsync maxmemory-policy
export REDIS_PASSWORD="$REDISCLI_AUTH"
nohup redis_exporter --redis.addr=redis://127.0.0.1:6379 \
  --web.listen-address="$REDIS_IP:9121" \
  > /measure/$CAMPAIGN/redis-exporter.log 2>&1 &
```

普通回放使用AOF+everysec+noeviction。保存/迁移组在开跑前执行`redis-cli -h 127.0.0.1 CONFIG SET appendfsync always`并保存返回结果；用这一配置测端到端保存，不能把everysec的确认当作每次已落盘。服务重启后重新核对，不依赖动态配置自动保留。[Redis持久化说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)

redis_exporter不启用check-keys/count-keys或全库扫描；只取INFO等常规计数。监控密码通过环境注入。[Exporter参数](https://github.com/oliver006/redis_exporter)

## 4. 启动RustFS

在s0容器：

```bash
mkdir -p /data/rustfs
set -a
. /secrets/rustfs.env
set +a
export RUSTFS_ADDRESS="$S3_IP:9000"
export RUSTFS_CONSOLE_ENABLE=false
export RUSTFS_OBS_ENDPOINT="http://$MONITOR_IP:4318"
export RUSTFS_OBS_SERVICE_NAME=rustfs-s0
export RUSTFS_OBS_ENVIRONMENT="$CAMPAIGN"
export RUSTFS_OBS_METRICS_EXPORT_ENABLED=true
export RUSTFS_OBS_TRACES_EXPORT_ENABLED=false
export RUSTFS_OBS_LOGS_EXPORT_ENABLED=false
export RUSTFS_OBS_METER_INTERVAL=1  # 1.0.3代码中单位为秒
nohup rustfs /data/rustfs > /measure/$CAMPAIGN/rustfs.log 2>&1 &
```

这是单服务端、无RustFS自身冗余的测试配置；底层卷的保障另记。先检查进程持续存活，再从worker验证S3 PUT/GET/Range/LIST：

```bash
set -a
. /secrets/s3-client.env
set +a
export AWS_ACCESS_KEY_ID="$ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$SECRET_KEY"
export AWS_DEFAULT_REGION=us-east-1
python3 "$AX/infra/alicloud/deploy/s3_probe.py" --endpoint "http://$S3_IP:9000"
export JFS_BUCKET="ax-juicefs-$CAMPAIGN"
aws --endpoint-url "http://$S3_IP:9000" s3api create-bucket --bucket "$JFS_BUCKET"
```

`s3_probe.py`需要boto3；只创建和清理随机命名的探测桶。正式桶单独保留，不运行全服务清空命令。

四存储容器扩展：每容器申请4个真实独立数据卷；四台使用相同访问密钥、版本和完全相同的volume列表，例如：

```bash
rustfs 'http://10.72.0.31:9000/data/rustfs{0...3}' \
       'http://10.72.0.32:9000/data/rustfs{0...3}' \
       'http://10.72.0.33:9000/data/rustfs{0...3}' \
       'http://10.72.0.34:9000/data/rustfs{0...3}'
```

各容器的ADDRESS改为本机IP，OBS_SERVICE_NAME分别为rustfs-s0…s3。卷、端口及成员识别通过实际启动验收；同一卷下建4个目录不算4块盘。客户端固定同一个已确认S3入口，不临时增加未计量的代理。[官方多节点部署](https://docs.rustfs.com/en/installation/linux/multiple-node-multiple-disk)

## 5. 格式化一次，每个worker挂载一次

在w0，只对本轮独占、空Redis DB 0执行：

```bash
export META_PASSWORD="$(cat /secrets/redis.password)"
export REDISCLI_AUTH="$META_PASSWORD"
test "$(redis-cli -h "$REDIS_IP" -n 0 DBSIZE)" = 0 || exit 1
juicefs format --no-update --storage s3 \
  --bucket "http://$S3_IP:9000/$JFS_BUCKET" --trash-days 0 \
  "redis://$REDIS_IP:6379/0" "ax-jfs-$CAMPAIGN"
```

执行前确保已经载入上一步ACCESS_KEY/SECRET_KEY。DB非空就停止检查，不使用FLUSHDB或`--force`。JuiceFS的S3钥匙从环境读取，Redis密码用META_PASSWORD。[固定版本format实现](https://github.com/juicedata/juicefs/blob/v1.4.2/cmd/format.go)

每个worker：

```bash
export META_PASSWORD="$(cat /secrets/redis.password)"
mkdir -p /workspace/jfs /cache/juicefs/$CAMPAIGN /measure/$CAMPAIGN
nohup juicefs mount --metrics "$SELF_IP:9567" \
  --cache-dir "/cache/juicefs/$CAMPAIGN" --cache-size 1024 --buffer-size 256 \
  "redis://$REDIS_IP:6379/0" /workspace/jfs \
  > /measure/$CAMPAIGN/juicefs.log 2>&1 &
mountpoint /workspace/jfs
curl --fail "http://$SELF_IP:9567/metrics" > /measure/$CAMPAIGN/juicefs-before.prom
mkdir -p /workspace/jfs/runs/$WORKER_ID
```

不加`--writeback`。每个worker缓存上限1 GiB、读写buffer 256 MiB，其余参数按固定版本默认值保存；BrewFS比较时对齐总缓存预算。实际挂载、chown/链接/SQLite语义和跨客户端读回都通过后才跑完整轨迹。JuiceFS指标由客户端直接提供。[监控说明](https://juicefs.com/docs/community/administration/monitoring/)

## 6. 全部容器启动资源采集与结果发送

```bash
nohup python3 "$K/scripts/resource_exporter.py" --bind "$SELF_IP" \
  --worker-id "$WORKER_ID" --out /measure/$CAMPAIGN/resources.jsonl \
  > /measure/$CAMPAIGN/resource-exporter.log 2>&1 &
nohup python3 "$K/scripts/artifacts.py" ship \
  --url "http://$MONITOR_IP:8088" --root /measure/$CAMPAIGN \
  --worker-id "$WORKER_ID" --token-file /secrets/obs.token \
  > /measure/$CAMPAIGN/shipper.log 2>&1 &
```

接收目录按campaign、worker、相对路径隔离。每10秒发一次新增内容，1 MiB分块，SHA256核对、接收端fsync后确认；断连重试可去重。只发送指定的结果文件，不发送trace/blob、工作区或凭据。服务日志用软件各自`.log`文件名保留本地，批末统一打包；实时链路重点保住资源样本、任务stdout/stderr和结果JSON。

monitor本机检查`http://127.0.0.1:9090/api/v1/targets`：JuiceFS、Redis、resources、RustFS各job均须up；并实际出现RustFS指标而非只有OTel端口可达。再进入[数据下载与回放](workload.md)。
