# 历史归档

当前执行入口是[本轮方案与操作手册](../experiments/retest-2026-10-29/README.zh.md)。本目录只保留历史，不参与当前脚本的 import 或自动调度。

| 归档 | 内容 | 逐文件清单 |
|---|---|---|
| [legacy-20261028.zip](legacy-20261028.zip) | 原重复仓库、旧四 RQ 方案/报告和过时参数；181 个文件 | [manifest.json](manifest.json) |
| [legacy-code-20261029.zip](legacy-code-20261029.zip) | 旧 campaign/adapter/worker；旧 Terraform、三机控制器、Ceph/磁盘换挂；旧参数网格与专属测试；121 个原件 | [legacy-code-20261029.manifest.json](legacy-code-20261029.manifest.json) |
| [plans/2026-10-28](plans/2026-10-28/README.md) | 前一版实验方案 | 原文件与版本记录 |

10 月 29 日归档中，117 个已退役文件从日常目录移除；另 4 个是修改前的混合测试/说明原件，保留归档中对应旧代码的测试。当前仍需要的挂载、回放与 P1/P2 资格代码移至 [bench/reference](../bench/reference/README.md)，其测试继续运行。当前 Python 工具由 `harness/` 移至 `bench/`，实验 runbook 与 prompts 由 `infra/experiment/` 移至本轮目录，共用后端工具由 `infra/alicloud/deploy/` 移至 `infra/rustfs/`。

这些删除依据当前方案和调用关系，未按成绩好坏筛除实验数据。原始输入、结果、失败记录、冻结源码/资产摘要不修改。历史证据保留当时的路径与 SHA，不回填新位置。

每份清单保存原路径、字节数、SHA256、压缩包 SHA256 和来源 commit。验证：

```bash
python3 scripts/check_repository_archive.py
```

只查文件可在 ZIP 中按原路径查阅；恢复旧运行环境则检出清单所列完整 commit，保留当时的目录与依赖。若仅解包，使用仓库外的新目录，例如：

```bash
python3 -m zipfile -e archive/legacy-code-20261029.zip /tmp/ax-history-20261029
```

不要把旧控制器解压覆盖当前代码后直接运行，也不要用新参数解释历史成绩。后续旧版本优先由 Git 历史追溯，不再复制整仓库。
