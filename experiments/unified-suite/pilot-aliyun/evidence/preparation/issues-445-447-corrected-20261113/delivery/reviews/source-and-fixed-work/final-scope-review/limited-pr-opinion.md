# 限定范围的 PR 意见

2026-11-13（Asia/Shanghai）。意见：支持提交本轮限定范围的修复 PR。支持的内容是独立 Packed GroupMeta 填充并发、行缓存字节上界、cached-write 定向计算成本改善，以及共同的 writeback commit/cleanup 正确性修复；不支持“通用写性能全面提升”或“所有场景均无回退”的表述。

此次判断建立在已验证的正确性、目标路径收益，以及同工作量完整 tool+strict-drain 成本上。fixed 610/611/612 的 575 个归档成员已逐 SHA 复核，使用归档内同一冻结 assessor 重新解析六臂；每臂恰好写 4 GiB、1024 次 I/O，原三个 CPU 边界、严格排空、全新缓存冷重挂 SHA、服务及容量门全部通过。没有重新运行负载，也没有改二进制、预算或生产代码。

| 固定工作量完整成本 | baseline 中位数 | candidate 中位数 | candidate / baseline |
|---|---:|---:|---:|
| tool + strict-drain CPU | 10.84 s | 10.86 s | 1.001845 |
| CPU / GiB | 2.710 s | 2.715 s | 1.001845 |
| tool + strict-drain wall | 42.370633 s | 42.329959 s | 0.999040 |

上述比率采用两臂绝对中位数之比。三对 AB/BA/AB 的完整 CPU 比分别为 0.994465、1.014856、0.998162，完整 wall 比为 0.982690、0.999040、1.006041。它们支持限定的整体成本判断，本身不构成新的加速主张。

必须保留阶段变化：610 的 active wall 从 4.962625 s 增至 14.703115 s，drain 从 37.603284 s 缩短为 27.125994 s；完整 wall 从 42.565909 s 降至 41.829109 s。三对 active 比为 2.962770、1.000691、1.053479。这些数据不支持“普遍前台延迟改善”。用户已明确允许以整体改善验收阶段取舍，不能据此删除阶段原值。

目标收益证据与该 guard 分开解释：实际 FUSE 的 8 个独立 GroupMeta、8 线程受控延迟读取中，六对后端并发均从 1 提升至 8，读取耗时约为原来的 17%–19%；同 key 仍保持 single-flight。行缓存严格字节上界有实际压力采样与独立审查，长轮 128-key 负载覆盖真实热路径；cached-write 的重复实际 CPU 收益已有独立复核。这些是特定路径的证据，受控 proxy 延迟不是普通 S3 端到端延迟的估计。

以下范围及代价必须随 PR 明示：

- 短 metadata 场景仍有成本：按统一绝对中位数口径，单 key/单线程 fill 约 +6.17%，短 128-key/8-thread row 约 +18.51%；全部逐对值保留。长轮结果不能覆盖掉短轮结果。
- 原 601/603 完整累计历史矩阵中 randwrite/direct1 CPU/GiB 的 +28.60% 和 +11.42% 原样保留，不能将 fixed 的不同初态结果混平均为“原矩阵通过”。
- 605/606 已用真实旧文件 stat、chunk 编码与开始/完成日志证明前驱 compaction 进入目标 active/drain 窗口。独立调用栈审查又确认显式 compaction 样本与这些旧任务时间吻合。它解释了混入机制，不精确重建旧 601/603 未采样的每秒 CPU，也不声称解释了全部差额。
- fixed 使用每臂 fresh namespace 和相同逻辑工作量，保留默认 compaction；fio 原有 buffer/timestamp 语义不变，不能声称两臂写入内容逐字相同。其 scope 是本次前台与 strict-writeback-drain，未测后续完整 namespace 维护生命周期。

交付身份：实际 baseline 为 `4f982f45c153a0c31e3e9cd8ff642ec7659c98fd`，candidate 为 `c8f5f16aefe811540782b952481982179a1bfd6f`；发布源 `e2c964c55fafdcced34af58e840ebddda1cf9ea4` 仅额外包含独立证明的 test-only lint 修正。最终是否创建或推送 PR 由 root 执行，本意见没有代为提交。

可复算证据：

- `final-scope-review/fixed11-readback.json` SHA256 `921f0d37eb8240e262f870174c66b66728ba94e1b7a8e51cab63431e88437599`；脚本 `review_fixed11.py` 使用原冻结 assessor。
- `profile09/events.json` SHA256 `fbde2a9ae148569693019a84a7489f7ab12444e4421a3ccfaddfc8085b58f644`，`profile09/review.md` SHA256 `4744f60fb548252defbde02559298f583aaf4c382e3f0cbb885aa82696d43caf`。
- 原始 fixed 包 `corrected-runtime-review-11.tar.gz` SHA256 `bef783c9277a6d4a8c232e0b999e5e14882ac4ad456eecd8a54216f14be124ef`。

本意见不修改原始失败回执、旧回退数据或历史 HOLD 决策记录；限定准入是基于新增证据和用户已明确的整体成本取舍作出的后续判断。
