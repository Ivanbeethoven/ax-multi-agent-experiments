# 数据清单与下载

更新：2026-11-12。

## 数据位置

实验数据已备份到 [Google Drive 文件夹](https://drive.google.com/drive/folders/1TsB-GoxNHMhdn-IWTE5S0nQCMeJwro9X)。共27份任务trace：7份历史负载和20份扩展负载；备份清单共137个文件、13,654,428,716字节，另含元数据、脚本和校验清单。Drive下载需要有权访问该文件夹的Google账号；当前不是匿名公开链接。20份扩展trace还保留有阿里云OSS副本，下载需要有权限的Aliyun CLI身份。

7份历史trace及配套文件已恢复到 `oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023/`：28个对象、10,480,588,662字节。默认从OSS匿名HTTPS下载，无需账号；杭州云机可用internal地址，减少公网传输费用。恢复时核对了原始本地SHA256、上传后大小及本地/服务端CRC64，见[恢复回执](validation/historical-oss-restored/restoration.json)。11 月 12 日[匿名复查](validation/historical-oss-restored/public-readback-20261112.json)确认全部 28 个对象可访问，大小和校验元数据一致，7 份 trace 都能按字节范围读取；7 个大归档没有重新下载全量。另已[完整下载并核对 889,165 B 的元数据包](validation/historical-oss-restored/manifest-audit-20261112.json)，其中 3 份原清单都能对上归档和文件清单的大小及 SHA256；前 4 份独立原清单已经匿名全文校验。全部下载地址以 [`archives.json`](archives.json) 为准；后补的 3 份 trace 的 `archive-manifest.json` 在同目录 `metadata.tar.gz` 的 `metadata/cases/` 下，不是压缩包旁边的独立对象。此前删除保留在[历史迁移记录](validation/drive-migration-2026-11-10.json)，不代表当前状态。

扩展trace还在另一个OSS前缀：`oss://brewfs-tikv-100k-20260928/agent-fs-traces/20261029/`。2026-11-10盘点为88个对象、2,945,879,903字节；其中20份扩展trace的归档在Drive和OSS清单中的大小、SHA256一致。其余10个小型目录、回执和探测文件已从OSS取回本地，并尝试复制到Drive；Google API请求限额挡住了远端复核。按当前决定，88个对象先保留在OSS，不再执行删除。补充记录见[清理状态](../../experiments/drive-backup-2026-10-29/handoff/aliyun-legacy-expansion-2026-11-10/retirement-status.json)。OSS桶为私有桶，匿名访问返回403。

## 七份历史负载

Pytest是同一道题的两次独立运行。I/O归档在Drive中分成9个连续分卷；下载脚本会先校验分卷，再合并并核对原始SHA256。

| case ID | Drive中的文件 | 解压目录 | 回调数 | 压缩字节 |
|---|---|---|---:|---:|
| django | [django.tar.gz](https://drive.google.com/file/d/1s2HaDo0vm6tby65BnQmYpO3bp6HhPQ6G/view) | django | 57,787,980 | 2,302,532,371 |
| sklearn | [sklearn.tar.gz](https://drive.google.com/file/d/1O8KZN3iJd7VGI6YA1xDPADIhcotp3uCg/view) | sklearn | 9,233,326 | 842,598,913 |
| sympy | [sympy.tar.gz](https://drive.google.com/file/d/1jk0dVuliW5m_f81xrqsCvxqxN-9LTS-C/view) | sympy | 7,265,338 | 590,808,335 |
| pytest-full-02 | [pytest-full-02.tar.gz](https://drive.google.com/file/d/18QLIb40AsnETWJByfb2PC8jObpq-MqfF/view) | pytest-full-02 | 1,747,109 | 407,606,955 |
| pytest-20261026 | [pytest.tar.gz](https://drive.google.com/file/d/1PP0n3ZlF_bVu1hJ8LGnq_9-62b7OxJc2/view) | pytest | 2,836,142 | 185,203,598 |
| ripgrep-20261026 | [rust.tar.gz](https://drive.google.com/file/d/1NByKw4KrcFV35Dy-SpqSRMvxAxtrx4JR/view) | rust | 3,122,195 | 1,325,864,032 |
| archive-io-20261026 | [9个分卷及恢复清单](https://drive.google.com/drive/folders/1TlACuZfIVp2hwVz0GokTYRGSp2-_adfT) | io-compatible | 3,673,814 | 4,752,420,807 |

另有 [metadata.tar.gz](https://drive.google.com/file/d/1PzSKsmZk1lxxcQwJDHkASoARVZGU8UgY/view)，889,165字节，包含采集与回放报告、统计、文件清单、脚本和工具快照。

七份压缩包共约9.69 GiB。`archives.json`记录每份输入的完整SHA256、Drive位置和原OSS来源。Drive备份清单的137个文件均已核对大小和MD5；能返回Drive SHA256的文件也已核对。I/O分卷合并后为4,752,420,807字节，SHA256为`a4ca1c9e50c7c6cae9bf7f29e2d67cd88777e8fba3934ed5fbb646cf0ce77fb2`。

## 另外20份扩展负载

| Benchmark | 数量 | Case ID |
|---|---:|---|
| API-Bank | 2 | `api-bank-meeting-01`, `api-bank-salary-01` |
| AppWorld | 4 | `appworld-bills-analysis-01`, `appworld-export-01`, `appworld-playlist-query-01`, `appworld-ratings-update-01` |
| BFCL | 4 | `bfcl-missing-parameter-01`, `bfcl-trading-01`, `bfcl-travel-01`, `bfcl-vehicle-01` |
| InfiAgent-DABench | 2 | `dabench-39-01`, `dabench-62-01` |
| MLAgentBench | 2 | `ml-vectorization-01`, `ml-cifar10-01` |
| OfficeBench | 2 | `office-excel-01`, `office-pdf-word-01` |
| Spider2-DBT | 2 | `dbt-retail-01`, `dbt-divvy-01` |
| Terminal-Bench | 2 | `tb-access-logs-01`, `tb-csv-parquet-01` |

每份扩展负载位于 `traces/expansion-20261029/<case>/<case>.tar.gz`。完整Drive路径、文件大小和SHA256见[扩展负载清单](../../experiments/trace-expansion-2026-10-29/drive-catalog.json)；按题下载脚本为[download_from_drive.py](../../experiments/trace-expansion-2026-10-29/download_from_drive.py)，使用方法见下方。

## 下载

历史7份默认使用公开OSS，不需要rclone。命令见下方；在杭州云机下载时加`--internal`。若改用Drive备份，加`--source drive`，先安装[rclone](https://rclone.org/)并用有权限的Google账号配置只读remote：

```bash
rclone config create axdata drive scope=drive.readonly root_folder_id=1TsB-GoxNHMhdn-IWTE5S0nQCMeJwro9X
```

首次配置时，按提示用有文件夹读取权限的Google账号完成授权。若remote已存在，确认其账号和`root_folder_id`后直接使用，不要重复创建。

在实验仓库根目录下载并核对单个pytest输入：

```bash
export K="$PWD/scenarios/k8s-containers"
python3 "$K/scripts/download_archives.py" --out /input/archives --cases pytest-full-02
python3 "$K/scripts/check_archives.py" /input/archives --cases pytest-full-02
```

下载全部7份历史负载和元数据：

```bash
python3 "$K/scripts/download_archives.py" --out /input/archives --include-metadata
python3 "$K/scripts/check_archives.py" /input/archives
```

按SHA256下载并校验全部20份扩展负载：

```bash
python3 experiments/trace-expansion-2026-10-29/download_from_drive.py --out /input/expansion
```

只取一份扩展负载（默认从Drive下载）：

```bash
python3 experiments/trace-expansion-2026-10-29/download_from_drive.py \
  --cases ml-vectorization-01 --out /input/expansion
```

Drive临时不可用时，可从保留的私有OSS副本下载同一份扩展负载。先在机器上配置获授权的Aliyun CLI身份，再运行同一个下载器；文件仍按大小和SHA256校验：

```bash
python3 experiments/trace-expansion-2026-10-29/download_from_drive.py \
  --source oss --cases ml-vectorization-01 --out /input/expansion
```

扩展负载的OSS URI、大小和SHA256见[扩展负载清单](../../experiments/trace-expansion-2026-10-29/drive-catalog.json)。历史7份用`download_archives.py --source oss`，扩展20份用`download_from_drive.py --source oss`；后者所在桶仍是私有桶。

需要备份文件夹内全部137个文件（包括元数据、脚本和所有trace sidecar）时：

```bash
rclone copy axdata: /input/drive-backup --checksum --transfers 4
```

只下载889 KB的报告与元数据：

```bash
python3 "$K/scripts/download_archives.py" --out /input/archives --metadata-only
```

下载历史目录里的所有归档、文件清单和工具文件：

```bash
rclone copy axdata:traces/historical-oss /input/historical-oss
```

查看下载器将取哪些文件，不访问Drive：

```bash
python3 "$K/scripts/download_archives.py" --list
```

历史下载器默认直接读取公开OSS HTTPS，核对大小和SHA256后才改成正式文件名；`--source drive`改用rclone。OSS中的I/O包是完整单包，无需合卷；Drive备份才需要9卷合并。已有文件先校验；坏文件、未完成文件或已有I/O分卷目录会报错，不覆盖现场。Drive凭据不写进仓库、日志或结果包。

## 解压和回放

case ID和解压目录可能不同，例如`pytest-20261026`解压后是`pytest`。校验成功后：

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

JuiceFS先按[部署说明](deployment.md)完成挂载；BrewFS使用实际挂载路径替换`workspace-parent`。输入和测量结果放在被测文件系统之外。回放结果按原计划单独保存，不写回历史输入目录。

`campaign.py`仍使用旧4份trace。要把新3份加入实验，先更新并冻结任务表，记录case ID、输入SHA256和文件系统；不能只下载数据就算完成了7份对比。
