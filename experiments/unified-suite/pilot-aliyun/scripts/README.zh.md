# AX 实验脚本入口

**实验已暂停，原 A/B 测试机已释放，原跳板机保留。** [结果表](../../current/results-20261112/RESULT.zh.md)只含指标和关键回执。下列运行入口留待恢复时使用；旧机器 ID、盘和锁配置不可直接复用。

## 准备输入

已有输入先按清单核对，匹配就直接复用。需要补 `pytest-full-02` 时，只在杭州跳板机下载一次，再从跳板通过私网 SSH 分发到缺输入的 worker；[归档、SHA-256 和其他用例](../../../../scenarios/k8s-containers/archives.json)均在清单。

```bash
# 跳板机：REPO 为仓库绝对路径；WORKER_ALIAS 指向该 worker 的私网地址
python3 "$REPO/scenarios/k8s-containers/scripts/download_archives.py" \
  --source oss --internal --cases pytest-full-02 --out "$JUMP_INPUT"
scp -F "$PRIVATE_SSH_CONFIG" "$JUMP_INPUT/pytest-full-02.tar.gz" \
  "$WORKER_ALIAS:$WORKER_ARCHIVE"

# worker：NEW_INPUT 必须是新目录；TRACE_SHA 取自 archives.json
P="$REPO/experiments/unified-suite/pilot-aliyun/scripts"
python3 "$P/prepare-cloud-trace.py" --archive "$WORKER_ARCHIVE" \
  --sha256 "$TRACE_SHA" --root "$NEW_INPUT" --assets "$ASSETS" --repo "$REPO"
```

[解包脚本](prepare-cloud-trace.py)核对归档 SHA、初态和本地回放，输出 `result.json`；这个回执只验证输入。`prepare-cloud-inputs.py` 会让节点直接下载 OSS，留作旧部署复现。需要补二进制时，先固定版本和 SHA，再用 [package-deployment.py](package-deployment.py) 打包、[install-deployment.py](install-deployment.py) 分发安装。

| 用途 | 入口 |
| --- | --- |
| C3 / C4 一万文件 | [生成和校验说明](../../current/TENK-INPUTS.zh.md) · [prepare-pilot-tenk.py](prepare-pilot-tenk.py)。两个输入各 10,000 × 4 KiB；用相应的独立清单做冷读回校验。 |
| C2 完整 trace | [单轮执行草稿](../evidence/preparation/c2-current-trace-a-draft-01/README.zh.md) · [run-c2-current-a.ps1](../evidence/preparation/c2-current-trace-a-draft-01/run-c2-current-a.ps1)。待恢复后先更新拓扑并验版本、输入和锁，再生成 Packed lower、回放并冷验。[run-c2-publish-only.ps1](run-c2-publish-only.ps1) 只查发布阶段。 |
| C3 / C4 复查 | [C3 云端抽样回执](../evidence/preparation/cloud-c3-script-fixes-20261112/README.zh.md) · [C4 原生派生运行器](../../../../bench/fork_burst.py) · [C4 跨机执行草稿](../evidence/c4-state-reopen-draft-01/README.zh.md)。原拓扑只作记录；一万文件的云上运行尚无验收。 |
| 归档结果 | 保存运行配置、指标原文、正确性、失败和卸载回执及 SHA。可公开的包用 [archive-cloud-to-oss.py](archive-cloud-to-oss.py) 走 OSS 内网并读回核对；含会话或私有配置的原档放私有位置。[归档示例](../OSS-ARCHIVE.zh.md) |
| 清理资源 | 停止负载、正常卸载，核实原进程退出；删盘前在盘外独立核验不可再生证据。按创建账本圈定本任务的实例和盘，使用 [release-evidence.py](release-evidence.py) 的 `freeze` / `check` 记录范围并核对释放；仅云 API 查不到相应 ID 才记为已释放。[清理与核验](../RELEASE.zh.md) · [账单核对](../evidence/progress/pilot-evidence-audit-20261112/README.zh.md) |

原 A 池收尾使用三个独立步骤：[冷备份](preserve-a-backend.py) → [精确释放](release-owned-a.ps1) → [云端确认消失](audit-retired-pool-a.ps1)。备份逐文件校验；符号链接只记录目标，不跟随。用户最后要求只交付结果，client 的原始包保留在 jump，本地大包下载已取消；释放记录明确标出此范围。释放脚本只接受创建回执中固定的三个实例和九块盘，保留 jump，不按名称前缀删除。这些固定 ID 的脚本只供本次清理查档；新部署须另建资源清单。

旧 Entity、native-v2、Flat 完整复制和 JuiceFS 混合读写只用于查档，不再调度。

## C2–C4 卡在哪里

| 项目 | 已验证与尚缺证据 |
| --- | --- |
| C2 | [最新发布回执](../evidence/preparation/c2-publish-progress-timeout-02/README.zh.md)：服务探针 0.397 s，HeadBucket 0.264 s，CreateBucket 6.430 s。Packed 发布跑满 300 s，看到 145 个对象、24,520,277 B，未找到 manifest；末次样本的发布主进程累计 CPU 约 1.92 s。完整 trace 尚未开始，尚不能把发布等待分到上传、读回校验和服务端磁盘。 |
| C3 | [云上抽样](../evidence/preparation/cloud-c3-script-fixes-20261112/README.zh.md)用 64 个文件完成四阶段、128 次内容校验。一万文件的输入已在本地验全；仍缺云上全量冷验和同条件对照。 |
| C4 | [两文件原生 fork / clone](../evidence/preparation/rbm-c4-published-20261112/README.zh.md)已通过冷读及隔离；没有一万文件配对计时。[云上跨机](../evidence/preparation/c4-state-reopen-a-b-result-01/README.zh.md)读回 894 项和相同的 OpenCode 导出，但目标子进程退出超时，缺目标零上传债务回执。[本机修复](../evidence/preparation/c4-local-export-closure-diagnosis-01/README.zh.md)尚未在云上复验。 |

当前缺项以 [C2–C4 未完成清单](../../current/PENDING.zh.md) 为准。
