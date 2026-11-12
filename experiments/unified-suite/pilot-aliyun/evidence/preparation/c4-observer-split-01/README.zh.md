# C4 观察者拆包：真实规模验证（132,939 个成员 / 22.47 GB）

`pack-c4-observer-split.py` 在真实 On1 观察树上跑通了一次。这是**打包工具的验证**，不是把失败的 On1 改判为通过——那一轮仍然没有 `finished.json`。

## 结果

| 项 | 值 |
| --- | --- |
| 输入 | `/srv/ax-c4-observer-scratch/o/c4-a-flat-on1`，132,939 个文件、22,469,160,960 B |
| 观察原文包 | `c4-a-flat-on1-objects-observer.tar.gz`，**379,794,692 B**，SHA256 `504e8450b20fd032053b0fd6e161035db300fc0508ad88251e725d304901236c` |
| 成员数 | **132,939**（索引为 NDJSON，一行一个成员） |
| 闭合包 | `c4-a-flat-on1-objects.tar.gz`，**552 B**，SHA256 `7327fd1fcb11755ffda3f597f81eb937ebccea69490dd316103f46b42f97c4c2`，1 个成员 |
| 墙钟 | **6 分 52 秒** |
| 峰值内存 | **269 MB**（流式解析索引，没有把 16 MiB 索引或 22 GB 原文读进内存） |
| 退出码 | 0，两个包都通过重新读取校验 |

## 为什么这解决了原来的失败

原单包归档的内部清单是 20,430,785 B / 132,944 个成员，超过冻结的 16 MiB 上限，`archive_check.verify` 直接拒绝。拆包后：

- 观察原文进自己的 tar，成员清单是**流式 NDJSON**（129 B/成员，13.3 万成员约 16.4 MiB），由本工具的 `verify_observer` 逐行读取，不受单 JSON 上限约束。
- 闭合与其他证据仍走**冻结的** `bench.archive_check.pack/verify`，清单只有 552 B，远低于 16 MiB。

也就是说：**没有改冻结校验器一分一毫**，只是把大块原文挪出了它的单清单模型。

## 产物位置

两个 tar 与回执在采样盘上：`/srv/ax-c4-observer-scratch/archives-split-01/`。回执 `c4-a-flat-on1-objects-split.json` 里的 `path` 字段记录的是打包时的 `/var/tmp/c4-split-01/...`，文件随后被移动到上述目录，**SHA256 不变**。

本次关机前没来得及把回执 scp 回本地（实例先停了），`captured-stdout.txt` 是打包命令在节点上的原始输出，键值与上面的表一致；下次开机时把 `/srv/ax-c4-observer-scratch/archives-split-01/c4-a-flat-on1-objects-split.json` 原样取回即可。

## 下一轮怎么用

1. 在新的 On1 轮里，归档阶段用本工具替代单包：先打观察原文包，再用冻结校验器打闭合包。
2. 仍然需要**新的清理 + 容量 + placement 窗口**重新准入；旧 `capacity.json` 的 `observer_scratch.at` 与 boot 都已过期。
3. 采样盘现有 76,795,600,896 B 可用，本轮产物占约 380 MB。
