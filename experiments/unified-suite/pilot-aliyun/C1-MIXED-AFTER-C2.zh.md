# B 组：C2 之后补 JuiceFS 混合读写

C1 只缺 `8e3d340ff23c3ece-r1-juicefs`。在同一 B 组复用已验 64 MiB 随机 I/O 基线和原 fio 合同；三对校准仍为关→开、开→关、关→开，5% 门槛及正式结果的读写 IOPS、延迟、同步延迟和冷读验收保持原规则。

协调节点已生成草稿并核对 17 份资格文件。它绑定现有 C2 的计划、B 组实际硬件、部署代码和预期的末轮 collector；生成时未访问执行节点。把缺失的 C2 结果作为输入时，实测拒绝启动，没有生成运行请求或工作节点输出。

本轮由 `ax-pilot-b-c2-to-c1-watch-01` 等待原 C2。它先核对同一次 C2 的正常退出、正式关闭和三类节点原档，再在 B 组空闲时封存六轮校准，最后启动一次原定 C1。等待期间不访问工作节点；任一步失败会保留回执并停止。

只有确认 watcher 未接管、原 C2 正常关闭且六轮原档已封存，才在协调节点人工运行以下命令：

```bash
P=/srv/ax-pilot-control
TOOL=$P/preparation/c1-mixed-after-c2-tools-01/continue-c1-mixed-after-c2.py
DRAFT=$P/parallel-control-01/drafts/pool-b-c1-mixed-after-c2-01/draft.json
RESULT=$P/parallel-control-01/launches/pool-b-juicefs-c2-01/result.json
RESULT_SHA=$(sha256sum "$RESULT" | cut -d' ' -f1)
sudo systemd-run --unit ax-pilot-b-c1-mixed-after-c2-01 --collect \
  --property=Restart=no /usr/bin/python3 "$TOOL" --runtime-repo "$P/code-15" run \
  --draft "$DRAFT" --draft-sha256 781935f81c9e8910784f7c3d95e896e52f9739247a7c0466b853e5a519902a64 \
  --completed "$RESULT" --completed-sha256 "$RESULT_SHA"
```

协调节点上的工具 SHA256 为 `3a9c32544480dce43a3c61ebd2b867e0a3c9f9a4b50cee5d48d5849b9becfb94`。启动时核对 C2 的正式回执和 SQLite、协调进程已退出、实际 collector 存活、共享物理锁无冲突，并重新查询 BrewFS/RustFS 指定分支。任一检查失败即停；不会重跑 C2、清理旧结果或创建资源。若 C2 没有正式完成，先审计 B 组的实际退出边界，不能用该命令直接接续。
