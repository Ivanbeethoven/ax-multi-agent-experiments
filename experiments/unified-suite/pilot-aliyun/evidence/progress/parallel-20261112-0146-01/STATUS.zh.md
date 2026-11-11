# 2026-11-12 01:46 进度

正式验收仍为 **14/28**：C1 9/10、C2 2/5、C3 2/4、C4 派生 1/5、C4 跨机恢复 0/4；22 项消融另计。本轮没有新的正式通过项，也没有重跑任何已验收单元。

- **A 组 C4 On1：** 已解决空间门槛并跑完测量；**归档失败，不是正式项**。观察者 2,954 个样本、15,385 秒窗口通过，objects 归档 379,864,420 B 打包完成，但内部清单 20,430,785 B（132,944 个成员）超过冻结的 16 MiB 上限，`archive_check.py` 拒绝。原始观察 22.47 GB 保留在采样盘，三角色收尾干净。证据见 [归档失败记录](../../calibration/a-c4-flat-on1-archive-bound-01/README.zh.md)。原因：5 小时 × 5 秒全采样产生 132,939 个文件，单包清单必然越界。新增一块只给观察者用的 100 GiB PL0 盘 `d-bp16jaiu2fcmbij8mg7i`（¥0.105/小时），挂 `/srv/ax-c4-observer-scratch`；四块原始 RustFS 数据盘、Off1 回执与 code-19 工具均未改动。观察者实测写入采样盘（5 秒一采样），对象盘 0 只剩数据增长。`--validate-only` 通过后在同一 15 分钟窗口内启动，unit `ax-pilot-c4-on1-01`，invocation `048f667dbca245a885100a1432d97d54`，观察窗口 18,000 秒。证据见 [a-c4-on1-prestep-01](../preparation/a-c4-on1-prestep-01/README.zh.md)。
- **B 组 C1 维护：** 原四盘按 serial/UUID 重挂、原 bind 与原 RustFS 容器拉起已实测通过（盘上可用 3.06 + 22.06 + 22.06 + 22.06 GB）。JuiceFS **`gc --threads 1` 已只读跑通**（第 18 次，`passed:true`）：确认 db 61 就是原 C1 卷（data 指向 B RustFS 桶、8 个 slice / 70.9 GB 活数据），扫描 20,187 个对象，**20,101 个、69,078,298,624 B 处于 pending delete**，真正 leaked 只有 3 个对象 12.6 MB。AOF 前后逐字节一致、无 JuiceFS 会话残留、client 全程未启动、两台已回 `StopCharging`。**没有执行 `--delete`**：69 GB 是实测候选，删除仍需一次审核决定。全部回执见 [b-c1-gc-maintenance-01](../preparation/b-c1-gc-maintenance-01/README.zh.md)。
- **C4 跨机恢复：** 只完成离线准备。新增可审查的 request 生成器与持双池锁的外层 driver 骨架（默认执行器只拒绝并打印命令），16 项离线测试通过；真实两端 spec、独立 relay、外层 cold/closure 与正式准入仍缺，见 [GAPS](../preparation/c4-native-move-preparation-01/GAPS.zh.md)。本机零模型 state-reopen 与一次本机真实 OpenCode 恢复通过，**不算跨机完成**。
- **清理与容量：** 本轮 On1 前置只做只读复核（`outcome=not-needed`）：00:18 已 apply 的四棵旧 C1 原文没有回来，也没有新的 staging；对象盘可用 29,283,082,240 B。

## 费用

新增资源累计估算 **约 ¥158**（06:20 起 A 池已停，费率降到约 ¥1.07/小时）。停机前 **约 ¥5.742/小时**（A 三台 ¥4.671 + B 保留盘 ¥0.966 + 新采样盘 ¥0.105），现在只剩 B 保留盘与新采样盘约 ¥1.07/小时；不含原跳板、网络、OSS 与账单取整。估算方法：以上一次报价快照 ¥138.14（2026-11-11T15:24:13Z）为基线，按 2.37 小时 × ¥5.742 递增；B 对象/元数据本轮多次短时开停机各约 1–3 分钟，已含在 ¥0.966 的盘费之外未单独计。

## 现场

A 三台与 B 三台全部 `Stopped / StopCharging`；原跳板运行；五库 `cell_leases` 为空；新增采样盘仍挂在 A objects 上（`In_use`，盘上保留 22.47 GB On1 观察原文与 379.9 MB 失败归档）。重启前置见 [pool-shutdown-20261112-0620](../preparation/pool-shutdown-20261112-0620/README.zh.md)。
