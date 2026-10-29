# 已完成的一次性操作

`recover-local-probe.py` 和 `resume-client-build.sh` 仅保存早期探针恢复、既有构建续跑的原始脚本。它们绑定历史目录和资源，不能当成通用入口重新执行。所涉本地容器已移除、构建已结束；原始日志和产物仍在本地保留目录。

当前可复用工具在 [scripts](../scripts/README.md)，验证状态在 [qualification-status.json](../qualification-status.json)。
