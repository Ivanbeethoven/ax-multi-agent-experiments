# 负载位置、下载与回放

更新：2026-10-26。共7份已记录负载，来自两批；Pytest是同一道题的两次独立运行。每份完整URI、SHA256、大小、case ID和解压目录统一保存在[archives.json](archives.json)。

## OSS路径

桶：`brewfs-agent-traces-20261023-4ccdae73`，杭州。

旧4份前缀：

```text
oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023/
```

新增3份前缀：

```text
oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023/deepseek-opencode-20261026/
```

| case ID | 前缀下的文件 | 解压顶层目录 | 回调数 | 压缩字节 |
|---|---|---|---:|---:|
| django | [django.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/django.tar.gz) | django | 57,787,980 | 2,302,532,371 |
| sklearn | [sklearn.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/sklearn.tar.gz) | sklearn | 9,233,326 | 842,598,913 |
| sympy | [sympy.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/sympy.tar.gz) | sympy | 7,265,338 | 590,808,335 |
| pytest-full-02 | [pytest-full-02.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/pytest-full-02.tar.gz) | pytest-full-02 | 1,747,109 | 407,606,955 |
| pytest-20261026 | [pytest.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/deepseek-opencode-20261026/pytest.tar.gz) | pytest | 2,836,142 | 185,203,598 |
| ripgrep-20261026 | [rust.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/deepseek-opencode-20261026/rust.tar.gz) | rust | 3,122,195 | 1,325,864,032 |
| archive-io-20261026 | [io-compatible.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/deepseek-opencode-20261026/io-compatible.tar.gz) | io-compatible | 3,673,814 | 4,752,420,807 |

点击表中归档名称即可匿名下载。所有7份压缩包合计约9.69 GiB，解压、输入副本和输出工作区另计。

新增批次另有[metadata.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/deepseek-opencode-20261026/metadata.tar.gz)，889,165字节，内含：

- `metadata/RUN-REPORT.md`：采集、回放和上传记录。
- `metadata/summary.json`：操作数、实际读写、重复读取等统计。
- `metadata/cases/<pytest|rust|io-compatible>/archive-manifest.json`：归档与文件清单的SHA256。
- `metadata/controllers/`和`metadata/skill/`：该批执行脚本及工具快照。

新增Rust任务为ripgrep 14.1.1构建/测试；I/O任务为12000个小文件、4个128 MiB文件的归档、解包、扫描和SQLite索引。元数据记录三份均通过回放。旧批OpenCode为1.19.4，新批为1.2.26；执行对比时固定输入并保留批次身份。

metadata中的io-unreplayable是较早一次失败记录，不属于已发布的3份有效输入。

## 公网访问

公网endpoint：`https://oss-cn-hangzhou.aliyuncs.com`。

2026-10-26已将此专用实验桶的全部对象开放匿名只读，并验证28个对象均可匿名Range GET，目录列表返回HTTP 200。此前的403是公开前的状态。

下载无需阿里云账号、密钥或签名链接。授权通过Bucket Policy提供，桶ACL保留private；实际匿名读取权限以Policy为准。公开动作仅含GetObject和ListObjects，上传、覆盖和删除仍需身份。自建RustFS的凭据另行配置。

直接下载URL示例：

```text
https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/full-fuse/20261023/deepseek-opencode-20261026/pytest.tar.gz
```

## 下载和校验

在实验仓库根目录执行。下面先下载新增Pytest和该批元数据，默认通过匿名HTTPS下载并检查SHA256：

```bash
export K="$PWD/scenarios/k8s-containers"
python3 "$K/scripts/download_archives.py" --out /input/archives \
  --cases pytest-20261026 --include-metadata
python3 "$K/scripts/check_archives.py" /input/archives --cases pytest-20261026
```

下载全部7份及元数据：

```bash
python3 "$K/scripts/download_archives.py" --out /input/archives --include-metadata
python3 "$K/scripts/check_archives.py" /input/archives
```

仅下载889 KB的报告与元数据：

```bash
python3 "$K/scripts/download_archives.py" --out /input/archives --metadata-only
```

只看清单，不访问OSS：

```bash
python3 "$K/scripts/download_archives.py" --list
```

下载器逐文件核对大小和SHA256后才将.partial改为正式文件名；已有正确文件跳过，已有错误文件或未完成.partial会报错，保留现场供检查。

需要使用已配置身份的Aliyun CLI时增加`--via-oss-cli`。仅在杭州OSS内网确实可达时，再增加`--endpoint https://oss-cn-hangzhou-internal.aliyuncs.com`；默认HTTPS下载不使用该endpoint参数。

## 解压和单任务回放

case ID和解压目录可能不同，例如pytest-20261026解压后是pytest。确认下载/校验成功后：

```bash
mkdir -p /input/cases /workspace/jfs/runs/w0 /measure/tasks
test ! -e /input/cases/pytest || exit 1
tar --no-same-owner --same-permissions -xzf /input/archives/pytest.tar.gz -C /input/cases
python3 "$K/scripts/run_case.py" --case /input/cases/pytest \
  --workspace-parent /workspace/jfs/runs/w0 \
  --out /measure/tasks/pytest-20261026-r1 \
  --binary /opt/ax-bin/full-fuse-replay \
  --run-id pytest-20261026-r1 --worker-id w0
```

JuiceFS先按[部署说明](deployment.md)完成挂载；BrewFS使用其实际挂载路径替换workspace-parent。原始输入和测量目录保持在被测文件系统之外。

元数据解压：`tar -xzf /input/archives/metadata.tar.gz -C /input/cases`，目标metadata目录须事先不存在。

仓库原campaign.py仍冻结旧4份trace。使用新3份时须扩展并冻结新的任务表，保留每项case ID、输入SHA256和系统映射；不能只下载新数据就声称旧计划已测试7份。

## 测试结果保存

新结果使用独立前缀：

```text
oss://brewfs-agent-traces-20261023-4ccdae73/k8s-results/<唯一campaign>/
```

保留manifest、SHA256、全部任务状态、原始样本、日志、指标和TSDB；不要覆盖原始输入前缀。

已对7份归档核对OSS远端大小及SHA256元信息，下载读取新批metadata；未重新下载或回放这些大归档。原核查见[oss-catalog-check.json](validation/oss-catalog-check.json)，公开后的匿名访问验证见[oss-public-access-check.json](validation/oss-public-access-check.json)，当前策略见[oss-public-read-policy.json](deploy/oss-public-read-policy.json)。
