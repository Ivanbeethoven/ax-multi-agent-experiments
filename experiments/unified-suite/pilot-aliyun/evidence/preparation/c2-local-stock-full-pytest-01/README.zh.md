# C2：原版 BrewFS 的完整 pytest trace 回放尝试

在一台 WSL 主机上，用原版 BrewFS `ca07159` 的普通 `workspace-v1` 实际挂载 FUSE。Redis、RustFS 使用缓存镜像，在本机独立启动，宿主机端口只绑定回环地址；RustFS 的四个存储目录在同一台主机上。本次没有 Packed lower，没有调用模型，也没有启动云资源。

- 输入：`pytest-full-02`，归档 **407,606,955 字节**，SHA256 和下载地址见[负载清单](../../../../../../scenarios/k8s-containers/archives.json)。初始树 714 项的内容、权限、链接目标及硬链接关系通过检查，导入耗时 **17.45 秒**。完整输入包含 **1,747,109 次回调**、**281,774,624 字节读**和 **194,706,661 字节写**。
- 结果：外层进程在预设的 **220 秒**处终止回放。最后一个进度点为 **177.80 秒、262,144 次回调、52,813,061 字节读、104,055,966 字节写**。这个进度点是已确认下限；到时限前未报告字节不符。最终文件树与重新挂载后的读回未验证。本轮不是正式样本，也没有重跑。
- 收尾：BrewFS 正常卸载并以 0 退出；本轮 Redis、RustFS 和临时目录已清理。历史[完整回放](../parallel-c2-native-03/README.md)使用 `workspace-native-v2`，不能当作本轮原版 `workspace-v1` 的完整结果。

四段各 65,536 次回调的实测速率约为 **2,564 / 2,469 / 2,026 / 702 次/秒**。若从云端窗口预留 5 分钟做其余操作，20 分钟和 30 分钟窗口分别需要整个回放平均达到约 1,941 和 1,165 次/秒；最后一段低于两者。本机前缀不能确定云端完成时间。若另获独占窗口，可把 30 分钟设为诊断上限，留出停止和取证时间，并保留未完成回执。

[回执](raw/result.json) · [回放命令](raw/strict-proof/command.json) · [输入和程序哈希](raw/strict-proof/started.json) · [原始进度](raw/strict-proof/stderr.log) · [归档校验](SHA256SUMS)

[原始驱动](run.py)使用同仓库的[隔离挂载脚本](../c4-workspace-v1-packed-fork-01/smoke.py)作为模板并校验其 SHA256；驱动运行前替换模板中的小样本任务，本次始终使用完整 pytest 数据。它引用回执中记录的 WSL 路径，需要本机已有对应归档解包、固定二进制和镜像，以及可用的 FUSE、Docker 和含 boto3 的 Python。复现命令在 **WSL** 中执行，结果目录必须是新目录：

```bash
ax_c2_repo=/mnt/c/Users/HXY/.cache/ax-pilot-20261111/publish-fsync-1911-01
ax_c2_out=$(mktemp -d /tmp/ax-c2-stock-result.XXXXXXXX)
sudo -n /home/hxy/.cache/brewfs-entity-validation-venv/bin/python \
  "$ax_c2_repo/experiments/unified-suite/pilot-aliyun/evidence/preparation/c2-local-stock-full-pytest-01/run.py" \
  "$ax_c2_out/result.json"
```
