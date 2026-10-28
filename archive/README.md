# 历史材料（不作为当前执行入口）

当前方案见 [最终方案](../paper-design/FINAL-PLAN.zh.md)。为避免重复仓库和旧方案混入执行，本目录保存整理前的完整文件，而日常目录已删除其展开副本。

- [legacy-20261028.zip](legacy-20261028.zip)：原 `experiments/ax-multi-agent/` 快照、旧四 RQ 方案/报告、过时参数与文档、旧校验器，共 181 个文件；其中 180 个展开文件已删除，旧校验入口改为当前检查器。
- [manifest.json](manifest.json)：每个原路径、大小、SHA-256，以及压缩包摘要和来源 commit。已逐文件解压读取并与删除前原件核对。
- 当前实验结果仍在 [experiments](../experiments/README.md)、[results](../results/) 和 [validation](../validation/)，原始 trace、结果及论文 submodule 未删除。

需要追溯旧引用时，按清单中的原路径在 ZIP 内找文件，或查看来源提交。历史证据中的路径与哈希不回填成当前版本。解压到仓库之外的新目录，避免旧入口覆盖现行文件：

```bash
python -m zipfile -e archive/legacy-20261028.zip /tmp/ax-history-20261028
```

未来只保留一套当前方案和脚本；旧版本优先由 Git 历史追溯，不再复制整仓库。
