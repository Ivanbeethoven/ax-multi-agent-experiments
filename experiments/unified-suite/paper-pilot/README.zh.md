# Pilot 数据到论文

本目录保存 2026-11-11 的小规模结果。原始数据只读保留；论文数值和图由同一个脚本生成。实验工具仍是 fio / Rust，Python 只做整理与绘图。

## 更新

安装 Python 的 matplotlib，在本目录运行：

    python render.py --data data/2026-11-11 --paper <论文仓库根目录>

输出到论文 figures/pilot/：正文数值宏、单栏 smallfile-costs 和 access-comparison 的 PDF / SVG / PNG。后者将八阶段 (a) 和配对配置 (b) 在一栏内左右并排，共用图例和横轴名称；时间除以同条件 JuiceFS 时间，虚线为 1，越低越好。准备/执行/核验仍采用三个独立秒数轴。未测 packed 配置不画柱；每配置一次观测，不绘制误差条。

扩大规模时新建日期目录，保留三份 JSON 的字段和单位，传入新的 --data；不要覆盖旧批次。数据中规模、线程数、操作数、时间和比率会一起更新。当前脚本遇到重复配对块会停止；加入重复实验后需先实现按块统计及置信区间，不能将单次图当作重复实验汇总。本批 10K、下一批 100K、之后 1M 保持独立记录。

## 数据口径

- c3_c4_10k_summary.json、pilot-summary.json：从 rbm 同名原账本复制。
- conditions.json：从同批 campaign 的 plan/report 配对提取每 cell 的 wall time，加实际 trace metrics 和 Rust smallfiles result 的配置。原路径和内容指纹见 data/2026-11-11/sources.json。
- C1 图使用 campaign wall time，顺序读 Redis 为 0.453 / 0.533 s；报告的 0.132 / 0.155 s 与这个计时范围不同。不能将 wall-time 比率称为 fio 吞吐比，也不能把普通 write 的 p99 直接当作 fsync p99。
- C2 实际是 140 操作的合成验证 fixture。服务器 metrics 与归档生成脚本一致；不是 1,747,109 操作的完整 pytest 回放。packed/native 为完整路径比较，尚未隔离 metadata representation。
- C3 的 10,000×4 KiB 是 40,960,000 字节，即 39.0625 MiB。采用 JSON 的 ext4 数值（执行 4.441 s、核验 0.0879 s）。初始读只命名 initial read，没有全层冷缓存证明；首次写比 JuiceFS 慢也保留。准备、执行、核验独立计时，materialize 原值保留在账本，不混入执行时间。
- C4 的 0.192 s 仅保留为账本中的创建探针。归档 run_remaining_10k.py 的 workspace 分支先 init-volume/init-native，再 fork，未填充同一万文件树；first-read、mutation 时间和 isolation 标记为常量。与远端最终汇总不是完整可追踪的一一对应记录。因此不采用“万文件 fork 快 7.64 倍”“<0.05 ms 首读”“已验证隔离”或 O(1) 的实证结论。
- 每配置只有一次记录。JSON 中六个 accepted blocks 是不同 workload×metadata 组合，不是六次重复。

## 下一批最少补什么

| 问题 | 条件与必须保存的结果 |
|---|---|
| RQ1 overlay | 同一已填充并校验的树；native-overlay 对比同引擎 eager metadata；分开 capture、fork、首读、首个 durable edit；保存父子/兄弟 hash，扫文件数、fan-out、深度。 |
| RQ2 packed | 同引擎 native-overlay / native-overlay-pack；固定只读 ML 小文件集，payload/cache 相同；单列 pack/build/upload/verify/publish、第一次 fork、复用次数；量 DB 请求/CPU/常驻元数据与对象页流量，并测旁路可变任务的 p99。 |
| RQ3 granularity | 独立改变 g、B、q，确认对象实际布局；加 device bytes、CPU、repair；旧云 VM 数据继续单独归档。 |
| RQ4/5 agent | 真正跨 host 迁移、继续有效工具调用；增加并发和保持分支；计失败、排队、deadline completion、回收；每条件独立配对重复。 |

正文保留“三个机制独立可选”。未测收益留为研究问题，不填预测值。

## 检查

源账本与服务器 SHA256 一致。生成器校验操作数匹配、正数计时、完整配对和账本比率；同批数值只在数据中维护。
smallfile-costs 为 240 × 72 pt，按 90% 栏宽插入，最小实际字号 7.65 pt（原 7.14 pt）；access-comparison 为 240 × 136 pt，按单栏宽插入，最小字号 8.6 pt。矢量 PDF 为论文输出，SVG 保留可编辑文字，300 dpi PNG 为预览。可传 --qa-tools <nature-figure/scripts> 指定对齐检查工具；本地没有该工具时只生成图，不声称通过审计。审计和全部 39 个原始秒数见论文 validation/paper-pilot/；正文引用 access-comparison 的 (a)/(b)，旧 standalone 图不再插入正文。

配色与论文 Figure 2 一致：BrewFS native 用 #D55E00，JuiceFS 用 #595959，ext4 用 #BDBDBD，packed 用白底空心柱；柱边与坐标轴为 #222222、0.65 pt。压缩留白，不缩小字号；PDF、SVG 均保留可编辑文字。

Figure 8 两个面板已改为单栏内同一行，高度较上下版减少 42.4%。R/W 缩写见图注；移除柱旁重复数值标注，完整阶段名和所有数值保留在 plot-data.json，不减少观测。
