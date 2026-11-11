# BrewFS flat随机写

1个Agent、1个fio job、64MiB输入、4KiB随机写、每次写均fsync，共16,384次写。BrewFS为原生干净main/622736b，RustFS为指定分支/f4ed202。

正式任务1780.242448363s，9.204944 IOPS。写调用P99为0.464896ms，fsync P99为109.576192ms。完整数据与冷读回检查、排空、原进程退出及完整监控均通过；三组校准的完成时间变化中位数绝对值约0.048%。这是单次试跑结果，JuiceFS对应项仍在校准，尚无该项配对结论。

原始包已下载到`C:/Users/HXY/.cache/ax-pilot-20261111/retained-native-randwrite-01/`，独立副本逐成员核验：

| 包 | 大小 | 已核范围 |
|---|---:|---|
| `formal/cell.tar.gz` | 101,314,707B | 外层及3个节点包，共73,767个归档成员 |
| `calibrations.tar.gz` | 143,647,131B | 六次校准、12个节点包，共110,460个归档成员 |

摘要和SHA见[正式核验](nested-archive-check.independent.json)、[校准核验](calibration-nested-check.independent.json)。完整原始包保留在本机和协调者，Git只存小型结果和校验记录。

独立核验命令（Linux）：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/verify-retained-bundle.py \
  --archive /absolute/path/cell.tar.gz \
  --sha256 9fb25f4ca713878fb94bcb33fe898f254601ca6c1ca6e48ab8ef71a8969d9ae8 \
  --out /absolute/path/new-check-directory
```

该工具逐层核验成员，将嵌套包另存到指定核验目录。指标见[io-metrics.json](io-metrics.json)，任务与同步耗时分开报告。
