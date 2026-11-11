# A 组原始校准归档

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

协调节点上的 `ax-pilot-pa-retain-calibrations-after-c3-01` 只等原 C3 flat 运行退出。它核对原 systemd invocation、SQLite 和同一正式关闭回执，通过后依次归档 BrewFS 随机读原六轮、混合读写原六轮、C3 flat 六轮。等待时不连接执行节点；没有新的测量、资源或重试。

原请求 SHA256 为 `7a03c3101581a7a06e1f1ac455228309e629a1a21ce840cce504f359a56c7445`，invocation 为 `86a9f95119ed4a34a88ce87c48cb6758`。原进程收齐随机读校准后遇到旧 Packed 校准记录：单元 ID 写在请求内，归档工具无法识别。核对原请求、失败回执、空槽位、无租约和进程退出后，续收混合读写和 C3 校准，复用已收好的随机读原档。

[续收脚本](scripts/resume-a-retention.py)于 17:17 正常退出，invocation 为 `6325dc1a9e0349caa47d2d9d3deb34ad`。[交接回执](evidence/c3-flat-01/retention.json)绑定三份原始包的核验记录。16 项归档检查通过。三份原档由协调节点直传 OSS，均已读回核对 SHA256 并登记[下载](OSS-ARCHIVE.zh.md)。
