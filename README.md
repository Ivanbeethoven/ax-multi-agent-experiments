# BrewFS 实验仓库

从 [最终实验方案](paper-design/FINAL-PLAN.zh.md) 开始。三个机制独立可选：Overlay、Packed Metadata、动态对象/后端粒度；共同读写对照为 BrewFS 三配置与 JuiceFS。

**当前状态：诊断执行链已重写并通过本地回归，正式五个 RQ 尚未放行。** 运行和剩余接口见 [阿里云入口](infra/alicloud/RUN.zh.md)。本次没有创建云实例或宣称云端端到端通过。

## 必要入口

| 内容 | 入口 |
|---|---|
| 五个 RQ、参数、顺序、计时和成本 | [最终方案](paper-design/FINAL-PLAN.zh.md) |
| 待修的测量问题 | [审阅清单](paper-design/REVIEW-20261028.md) |
| 部署与机器磁盘要求 | [部署](infra/alicloud/deploy/README.md) · [装置要求](paper-design/EXPERIMENT-CONFIG.zh.md) |
| 运行、观测与剩余能力 | [运行说明](infra/alicloud/RUN.zh.md) · [脚本](harness/aliyun_suite.py) |
| 真实 trace 下载和 Rust 回放 | [数据清单](scenarios/k8s-containers/data-catalog.md) · [Rust 回放器](tools/full-fuse-replay/README.md) |
| 采集、调度和监控 | [采集 Skill](skills/agent-fs-trace/SKILL.md) · [工具](scripts/) · [监控](scenarios/k8s-containers/observability.md) |
| 已有结果 | [结果索引](experiments/README.md) |
| 历史方案与迁入副本 | [历史压缩包和清单](archive/README.md) |

RustFS 必须使用 `Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased`；每批固定源码 SHA 和对应镜像摘要，核验实际数据路径。详细约定见 [AGENTS.md](AGENTS.md)。

## 校验，不启动实验

```bash
python paper-design/tools/check_design.py
python paper-design/pipeline/validate.py --self-test
python harness/final_plan.py --output runs/final-main.json
```

静态校验不代表运行资格通过。使用新生成的五 RQ 表；旧 design.json 展开表仅供历史追溯，不直接开跑。

`inputs/` 保存真实任务和评分材料，`workloads.json` 保留确定性 F1/F2 输入定义；`harness/`、`configs/`、`tests/` 保留依赖它们的参考执行器与测试。参考执行器中的旧任务表不是当前研究方案。

论文通过 [paper/](paper/) submodule 引用独立的 osdi2027 仓库；实验规划与脚本只维护在这里。历史结果、失败证据和原始 trace 保留原路径，不因整理而改写。
