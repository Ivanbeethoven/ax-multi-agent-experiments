# 扩展条件与上传策略消融

共用操作顺序、观测和结果保全见 [SSH-RUN](SSH-RUN.zh.md)。场景定义见 [SCALE](../SCALE.zh.md)。本页只列扩展矩阵的生成方式。

## 规模扩展

```bash
python3 bench/scale_plan.py --campaign ecs-20261101 --profile main --cluster C2 \
  --isolation docker --provider ssh --output "$RUN/C2-plan.json"
```

C1 当前用 pilot-8/c1-8；C2–C4 用 main。expanded 规划更大规模，vm 表示独立 VM 条件。逻辑放置通过实际 SSH 清单的 slots 落地，共享服务、宿主预算及 C4 目标端分别核验。

## 独立上传策略消融

```bash
python3 bench/scale_plan.py --campaign ecs-20261101 --study writeback --cluster C1 \
  --isolation docker --provider ssh --output "$RUN/C1-writeback.json"
```

C1 为 186 项，C4 为 90 项；这套消融独立于当前 C1 的 8 Agent 主组。C2/C3 保持只读或对象布局研究。各臂的实际配置写在 required_effective_config，执行前验证同一格式上的模式生效、暂存恢复和远端 drain。C4 另固定写后 checkpoint，并分别报告应用完成与远端可读时间。

## 可选 LLM 等待

`--include-llm-simulation` 追加有等待的多 Agent trace 条件，默认关闭。每个任务固定总等待 T，各工具调用或轮次之间的间隔随机分配、每段 0–15 秒，两臂共用计划 SHA。该消融只追加 C1 archive-I/O、64 Agent 的 Overlay/JuiceFS × 两策略 12 项。边界与 T 的生成方法见 [think-time](../../../tools/full-fuse-replay/THINK-TIME.zh.md)。
