# 实验仓库整理方案

制定日期：2026-10-28；基线提交：`829536c`。

这份方案先于任何删除或归档提交。盘点依据是 `git ls-files`（1,703 个受 Git 管理的文件）、各目录体积、当前文档引用、`git log --all -- <path>`，以及 2026-10-27 的讨论记录。讨论记录已通过用户给出的 share URL 抽取到本机临时文件并全文读完；它要求先校准资格、缓存和完成边界，再做 Overlay、Packed、对象布局、迁移和固定任务实验。

## 现状与分类

| 范围 | 体积 | 角色 | 处理 |
|---|---:|---|---|
| `paper-design/` | 932 KiB | 当前计划、参数、fixture、执行检查 | 保留；新增一份不使用代号的重写计划 |
| `infra/`、`scripts/`、`skills/` | 608 KiB | 部署、采集、可执行工具 | 保留；遵守 `AGENTS.md` 的 RustFS 分支 pin 合同 |
| `harness/`、`tests/`、`tools/`、`configs/` | 600 KiB | 可再运行执行器、测试和配置 | 保留；不是原始产物 |
| `experiments/aliyun-ai-trace/2026-10-27/` | 7.9 MiB | 2026-10-27 磁盘矩阵、ABBA 原始证据、汇总与报告 | 保留；报告和讨论记录直接引用其中的 `summary/source-data.csv`、ABBA 报告和证据快照 |
| `experiments/agent-fuse-observations/` | 1.1 MiB | 观测派生表、绘图脚本和 QA | 保留；`replay-trace-audit.md` 和论文计划引用它 |
| `experiments/cloud-smoke/2026-10-25/` | 118 MiB | 云上冒烟的原始日志、JSON、负载和报告 | 报告、环境和主结果保留；其中 75 MiB 的 `results/amp/` 原始快照归档为一个 `tar.zst`，留下 README、清单和解档入口 |
| `experiments/ax-multi-agent/` | 2.4 MiB | 迁移前的完整历史快照（根目录已有大部分同内容） | 暂不删除；`validation/repository-migration.json` 和历史规划文档仍把它作为来源，保留真实历史版本 |
| `results/opencode-full-fuse-pytest-10356-20261023/` | 122 MiB | 完整 FUSE 回调和写入载荷的七个压缩分卷 | 保留；README、技能文档和回放命令逐卷引用它，分卷本身已经是可校验归档 |
| `results/fs-trace-pytest-10356-20261023/` | 27 MiB | 小任务原始 strace、初始工作区和回放证据 | 保留；README 直接引用初始归档和失败记录 |
| `results/swebench-*`、`validation/` | 约 6 MiB | 任务结果、评分、构建和审计记录 | 保留；报告与验证清单引用具体文件，失败证据不能删除 |

“原始产物”是观测事件、原始 CSV/JSON、日志、状态快照、压缩输入和载荷；“报告与结论”是 README、`report.md`、`summary.json`、论文 PDF/TeX、审计和验证清单。前者优先保留并只做归档或去重，后者保留可读入口和替代关系。

## 现有引用和不可删除项

- `experiments/aliyun-ai-trace/2026-10-27/` 的 `disk-matrix/summary/source-data.csv`、`summary/calibration-observation.json`、`compose/matched-abba-01/REPORT.md` 和 `OVERLAY-OPT-PLAN.md` 被仓库报告和讨论记录直接链接。
- `experiments/cloud-smoke/2026-10-25/report.md` 链接 `results/summary.json`、`results/stable/*`、`results/cleandiag/*`、`results/trace-statistics.json`、`results/workload.jsonl`、`results/cloud-manifest.json`；`jfs-write-amplification.md` 还逐项链接 `results/amp` 中的快照和日志。
- `results/opencode-full-fuse-pytest-10356-20261023/README.md` 链接七个 `capture.tar.gz.part-*`、`bundle.json` 和回放入口；`skills/agent-fs-trace/references/{commands,repositories}.md` 也直接写出该目录。
- `results/fs-trace-pytest-10356-20261023/README.md` 链接 `initial-workspace.tar.gz`、三组 capture、失败前置检查和校验 JSON。
- `paper-design/README.md`、`design.json`、`reference/details.zh.md`、`plan.zh.md` 及 `validation/` 清单是当前计划和执行器的入口，不能用历史副本替换。
- `experiments/ax-multi-agent/` 虽与根目录存在大量同 SHA 文件，但它是历史快照；`experiments/paper-planning/` 和 `validation/repository-migration.json` 仍引用其路径，所以本轮不动它。

## 本轮执行清单

1. 保留上述证据和所有可执行目录；不改写历史，不创建云实例。
2. 将 `experiments/cloud-smoke/2026-10-25/results/amp/` 中的 154 个原始文件（76,893,265 字节，约 73.3 MiB）打成 `results/amp/raw-20261025.tar.zst`，同时写 `archive-manifest.json`（原路径、字节数、SHA-256、归档 SHA-256、生成命令和日期）。保留 `amp/README.md`、源码快照、汇总、清单和复算脚本，并把 `jfs-write-amplification.md` 的逐文件入口改为“清单 + 解档后原路径”，明确替代关系。
3. 运行归档完整性检查后，删除已进入归档的 154 个原始快照/日志文件；保留报告 README、清单和必要的汇总/复算脚本。该删除是可逆的，因为归档包含完整内容和哈希。
4. 在 `paper-design/` 新增重写计划，使用“基础资格、属性缓存与重复打开、同机四配置写入、分支与保留、Packed 访问与偿本、对象粒度、迁移与 fencing、固定任务、真实 Agent”这些可读名称；每节包含目的、装置/版本、负载参数、记录字段、判定标准、边界和不能回答的问题，并在末尾说明与现有 `plan.zh.md`、`design.json` 的一致处和差异处。
5. 每个逻辑步骤单独提交：先提交本方案，再提交归档和引用修复，最后提交实验计划和交付报告；每次提交前运行适合的校验。

## 删除判定

本轮没有可以安全直接删除而不留替代关系的报告、原始证据或可执行文件。`results/amp` 的原始小文件只因已逐字节归档、保留清单且报告提供解档替代入口才删除；其余重复快照体量小或承担历史版本语义，保留。

