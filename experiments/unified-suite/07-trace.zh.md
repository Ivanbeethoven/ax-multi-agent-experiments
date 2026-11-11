# C2：完整 AI trace 重放

> 历史协议，仅用于原冻结计划；新实验见[当前方案](current/README.zh.md)。

正式 8/16/32 Agent、错峰及云盘资源见[32 Agent 落地手册](MAIN-C2-COST-DISKS.zh.md)。以下保留原统一协议，原计划默认无等待。

四份trace×N=1/4/8，每个Agent一个完整任务，一次cohort释放。所有baseline使用相同输入摘要、initial树、操作顺序、真实写入blob和工作量；默认pacing=off。HOME/XDG/repo全部在该Agent专用workspace内，生成物不落宿主HOME。

## 准备与严格资格

使用 [输入步骤](04-inputs.zh.md) 核hash，按原始bundle重建initial树；不得过滤“对某个文件系统不利”的metadata/rename/unlink/fsync/SQLite操作。每种实际route先在独立新workspace做严格native replay与最终树校验，错误/不支持操作阻断该case，不跳过callback。

```bash
"$BIN/full-fuse-replay" "$TRACE_CASE/capture/events.jsonl" "$TRACE_CASE/capture/blobs" \
  "$STRICT_ROOT" "$OUT/strict-replay.json"
python3 bench/trace_checks.py tree --root "$STRICT_ROOT" --expected "$TRACE_CASE/final-tree.json" \
  --output "$OUT/strict-final-tree.json"
```

严格输出中的valid与退出都核对，记录metadata、内容、硬链接/链接目标等覆盖范围；SQLite只对离线复制出的DB/WAL/SHM做完整性检查，不在被测树里用数据库工具写状态。

## 性能段

使用 [trace request样例](../../bench/runtime/examples/trace-request.example.json)，每worker独立root、结果目录、workspace和mount绑定。生成器在屏障之前核大输入hash，避免将重复hash当成FS执行时间。

```bash
python3 -m bench.runtime.prepare trace-worker --request "$TRACE_REQUEST" --output "$OUT/trace-worker-draft"
# 依执行手册审读step，再更新worker引用；构造包含全部N个worker的cohort。
python3 -m bench.runtime.fleet cohort --request "$COHORT_REQUEST" --output "$RUN/cohort.draft.json"
```

性能模式仍重放全部callback，输出 `mode=performance, completed=true, valid=false`。valid=false是格式约定，不是内容校验通过。核全部callbacks、actual read/write bytes、remaining_handles=0；测量后对实际最终树和durable结果独立验收。

```bash
python3 bench/trace_checks.py replay --result "$OUT/replay.json" --metrics "$TRACE_CASE/metrics.json" \
  --output "$OUT/replay-counts.json"
python3 bench/trace_checks.py tree --root "$VERIFICATION_ROOT" --expected "$TRACE_CASE/final-tree.json" \
  --output "$OUT/replay-final-tree.json"
```

实际root由原生冷重挂/只读验证路径提供；不可拿另一个新initial workspace冒充本次结果。full replay重演文件访问，不重建原进程调度、模型响应和内核锁行为；C2完成时间不能称“真实Agent全任务加速”。原生Agent恢复能力在C4独立测。

取每任务wall、cohort共同wall、总操作/字节、完整/失败任务数、客户端CPU/RSS、元数据RPC、S3 GET/Range/PUT与实际字节。可选think time、跨trace混合队列继续使用已有专项工具，但本协议主表不在运行中启用它们。
