# 实验脚本入口

正式实验暂停。原 A/B 测试机已释放；历史机器 ID、盘和锁配置不可复用。当前范围与参数见 [C1–C4 方案](../../current/README.zh.md)，实际结果见 [性能结果](../../current/results-20261112/RESULT.zh.md)和[最小检查结果](../evidence/preparation/rbm-five-checks-20261112/README.zh.md)。

## 当前入口

按 [8 个实验块](../../current/BLOCKS.zh.md)分别执行；`bench.experiment_smoke_matrix --block ... --dry-run` 可先看完整命令，去掉 `--dry-run` 才启动最小检查。每块失败即停，独立资源池不受阻；旧失败结果保留。

| 工作 | 使用入口 |
|---|---|
| C1–C4 最小脚本检查 | [bench.experiment_smoke](../../../../bench/experiment_smoke.py)；[公共参数](../RBM-MINIMAL.zh.md) · [五项补充检查](../FIVE-CHECKS.zh.md) |
| Packed 关闭、全关闭 | 同上；候选源码、补丁、当前 `workspace` feature 和二进制须匹配能力回执，详见[五项检查](../FIVE-CHECKS.zh.md)。[write-five-smoke-provenance.py](write-five-smoke-provenance.py) 固定旧候选，仅用于历史复现；诊断候选不用于正式性能计时。 |
| 文件系统跨机重挂 | [prepare-filesystem-handoff-input.py](prepare-filesystem-handoff-input.py) 准备小输入；上述入口执行 source/target。只核文件和后续读写，不启动 Agent。 |
| 汇总小型证明 | [collect-five-smoke-results.py](collect-five-smoke-results.py)；保留失败和实际规模，不取整套 trace 或二进制。 |
| 每项临时目录清理 | `bench.experiment_smoke_matrix --scratch-parent ...`；独立预览/执行用 [bench.run_cleanup](../../../../bench/run_cleanup.py)，见[清理流程](../../current/CLEANUP.zh.md)。 |
| 一万小文件输入 | [prepare-pilot-tenk.py](prepare-pilot-tenk.py) · [生成和校验](../../current/TENK-INPUTS.zh.md) |
| C4 原生 fork / clone | [bench/fork_burst.py](../../../../bench/fork_burst.py)；当前功能结果与尚缺的正式性能证据见[未完成项](../../current/PENDING.zh.md)。 |
| 构建与内网分发 | [package-deployment.py](package-deployment.py)、[install-deployment.py](install-deployment.py)；固定源码、产物 SHA256 与镜像 digest。 |
| 指标采集 | [OBSERVABILITY.zh.md](../OBSERVABILITY.zh.md) · [bench/runtime](../../../../bench/runtime/README.md) |
| 归档 | [archive-cloud-to-oss.py](archive-cloud-to-oss.py) · [使用说明](../OSS-ARCHIVE.zh.md)；公开包先核内容，私有配置和会话原件另存。 |
| 资源核验与费用 | [release-evidence.py](release-evidence.py)、[record-cost.py](record-cost.py) · [清理步骤](../RELEASE.zh.md) |

## 下载并分发 trace

已有输入先核清单，匹配即可复用。补充 `pytest-full-02` 时在杭州跳板下载一次，再从私网分发。所有用例和 SHA256 见 [archives.json](../../../../scenarios/k8s-containers/archives.json)。

```bash
# REPO 为仓库绝对路径；WORKER_ALIAS 使用私网 SSH 配置。
python3 "$REPO/scenarios/k8s-containers/scripts/download_archives.py" \
  --source oss --internal --cases pytest-full-02 --out "$JUMP_INPUT"
scp -F "$PRIVATE_SSH_CONFIG" "$JUMP_INPUT/pytest-full-02.tar.gz" \
  "$WORKER_ALIAS:$WORKER_ARCHIVE"

# worker：NEW_INPUT 必须为新目录；TRACE_SHA 取自 archives.json。
P="$REPO/experiments/unified-suite/pilot-aliyun/scripts"
python3 "$P/prepare-cloud-trace.py" --archive "$WORKER_ARCHIVE" \
  --sha256 "$TRACE_SHA" --root "$NEW_INPUT" --assets "$ASSETS" --repo "$REPO"
```

[prepare-cloud-trace.py](prepare-cloud-trace.py)核对归档、初态和本地回放；生成的 `result.json` 只是输入资格。Rust 回放器见 [工具说明](../../../../tools/full-fuse-replay/README.md)。

旧池续跑、值守和固定 ID 的释放脚本，以及取消的 Flat 完整复制路径，已经从日常目录移除，见[退役清单](../../../../archive/README.md)。其余历史冻结脚本仍由旧回执或测试使用，不作为新实验的默认入口。
