# C4 flat：复用已完成冷验的 10 万文件基线

20:23 补充：随后审查发现对象盘预算漏计监控原文及归档副本。[容量缺口和处理记录](../c4-observer-capacity-gap-01/README.zh.md)已单列；本页原回执只覆盖数据副本预测。首轮继续运行，批次控制器已停在派发下一轮之前，待完整空间预算核验后再继续。

2026-11-11 19:57（北京时间），A 组通过新容量检查并启动后续校准。资格检查通过，第一轮已经派发。正式完成数仍为 13/28。

原基线导入和全量冷验已经通过。原容量检查因 Redis 重写期间磁盘占用下降而退出，详见[原记录](../c4-flat-aof-capacity-stop-01/README.zh.md)。这次保留原失败目录，复用同一基线，继续原定六轮校准和正式测试；测试输入、二进制、计时方法及顺序保持原值。

## 容量如何重算

[原始日志块](c4-aof-journal-04.json)和[独立校验结果](verification.json)确认：两份旧 AOF 与三代 manifest 合计释放 **605,442,048 B、5 个 inode**。日志块校验码、文件编号及代号均匹配。重写生成的临时文件已改名为当前 `.17.base.rdb`，没有重复计入释放量。

元数据盘每份副本的保守增量为：

```text
max(0, 导入后占用 − 导入前占用 + 已核实释放量) + 导入前全部占用
= 1,743,368,192 B
```

加上七份完整副本，再预留 **7,272,353,792 B** 给一次 AOF 重写，元数据盘预测占用为 **20,673,531,904 / 60,070,604,800 B**。RustFS 最紧的一块盘仍为 **47,212,527,616 / 60,070,604,800 B**。全部行通过原定 **20% 剩余容量**要求，见[新容量结果](capacity-01.json)。

这份预测用于本轮七份副本的准入。日志导出核实了上述五次文件回收；后续实际占用和重写峰值由运行时指标继续记录。

## 可复查文件

| 文件 | 内容 |
| --- | --- |
| `capacity-before.json`、`capacity-after.json`、`capacity.json` | 原始前后采样及原失败结果 |
| `c4-redis-files-01.json`、`c4-controller-window-01.json` | Redis 完整重写日志、存活文件、原控制脚本和退出记录 |
| `recovery.json`、`fresh-01.json` | 回收量依据及启动前同节点、同磁盘的新采样 |
| `quiescence.json`、`boundary.json` | 原进程已退出、挂载已撤销、上传已排空 |
| `preflight-01.json`、`reuse.request.reviewed.json` | 独立预检及精确输入校验值 |
| `launched.json` | 新控制进程、资格通过和第一轮派发记录 |

离线核验日志块，在仓库根目录执行：

```bash
d=experiments/unified-suite/pilot-aliyun/evidence/preparation/c4-flat-aof-capacity-recovery-01
s=experiments/unified-suite/pilot-aliyun/scripts
python3 "$s/verify-c4-aof-journal.py" \
  --input "$d/c4-aof-journal-04.json" \
  --producer "$s/audit-c4-aof-journal.py" \
  --out /tmp/c4-aof-verification.json
python3 -B -m unittest discover -s tests -p 'test_c4*.py'
```

本轮 57 项测试通过，覆盖日志篡改、文件代号、容量临界值、错误节点/磁盘，以及复用失败时不派发工作、成功时完整执行原顺序。现场恢复入口为 `run-c4-batch.py --reuse-request ... --reuse-sha256 ...`；请求绑定实际原路径及校验值，执行前重新核验状态。
