# A C4 On1（step 2）：测量完成，归档被冻结的清单上限挡住

2026-11-12 01:07 启动，unit `ax-pilot-c4-on1-01`，invocation `048f667dbca245a885100a1432d97d54`（systemd-run `--collect`，事后 unit 已回收）。**这一轮不是正式通过项**：没有 `finished.json`，因此不计入 14/28，也不重跑。

## 实际发生了什么

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| 观察者启动 | 通过，写入专用采样盘 | `ps` 显示 `bench.runtime.observe serve --output /srv/ax-c4-observer-scratch/o/c4-a-flat-on1` |
| 观察采样 | **通过**：2,954 个样本，窗口 15,385.08 秒 | `finished.json`（`passed:true`，`spec_sha256=fb7b941e…`，monotonic 80127356401460→95512439339540 ns） |
| measure-end / drain-end / verify-end | 三个标记都写出 | `calibration/flat-on1/marks/` |
| objects 归档 | 打包完成：**379,864,420 B** | `/srv/ax-c4-observer-scratch/archives/c4-a-flat-on1-objects.tar.gz` |
| 归档校验 | **失败** | `archive-stderr.txt` |
| client / metadata 归档 | 未执行（循环在 objects 处停止） | `failed.json` |
| 收尾 | 三角色 `safe_for_release: true`；采集容器已停；无残留挂载与进程；slot 为空 | `before-boundaries-objects-scratch.json`、现场核查 |

失败原文：

```
File "<pinned archive-c4-closed-observer.py>", line 237, in archive
File "<pinned archive-c4-closed-observer.py>", line 172, in pack_mapped
  File "/srv/ax-objects-0/code-19/bench/archive_check.py", line 37, in verify
    if member.size>16*1024**2:raise ValueError('unbounded internal manifest')
ValueError: unbounded internal manifest
```

## 根因（已量化）

`bench/archive_check.py` 只对内部清单 `.suite-manifest.json` 设了 16 MiB 上限。这次对象角色的清单是 **20,430,785 B（19.5 MiB）**，列出 **132,944 个成员**——因为 5 小时窗口、5 秒一采样把观察原文写成了 132,939 个文件、22,469,160,960 B。

也就是说：**冻结的 `closed-observer-direct` 归档在「5 小时全采样」规模下必然超限**，与工作负载本身无关。之前的 C4 轮次窗口短、观察原文小（几十 MB），从未触发这个上限。

## 这一轮留下了什么

- 观察原文完整留在 `/srv/ax-c4-observer-scratch/o/c4-a-flat-on1`（22.47 GB、132,939 个文件），采样盘可用 77,175,414,784 B；未删除、未覆盖。
- 对象归档 tar（379.9 MB）留在采样盘 `archives/` 下，作为失败现场。
- 专用采样盘路由在真实运行中被证明有效：观察者确实写在 `/srv/ax-c4-observer-scratch`，对象盘 0 只剩数据增长（运行前后可用 29,283,082,240→28,238,979,072 B，约 1 GB 的日志与临时量，与预算一致）。
- 三角色收尾干净：无 JuiceFS/BrewFS 挂载、无 fio/opencode 进程、client cgroup slot 为空、采集容器已停。

## 下一轮必须先决定的事

1. **清单上限与窗口规模不匹配。** 要么在**新一批**里给归档清单一个新的上限（或分卷/分包），要么把观察原文从单包里拆出去单独发布；两者都要新的 reviewed 工具与 pin，不能改 code-19。
2. 若沿用单包：132,944 个成员、19.5 MiB 清单已经实测越界，缩短窗口会违反原 Off1 的 13,372 秒下界，不可取。
3. 无论选哪条，On1 都需要在**新的清理+容量+placement 窗口**里重新准入；本轮的 `capacity.json` 已过期（`observer_scratch` 的 `at` 与 boot 都变了）。
4. 本轮不做清理：观察原文是下一轮判断「清单拆分还是上限」的输入，先保留。
