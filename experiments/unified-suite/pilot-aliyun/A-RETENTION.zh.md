# A 组原始校准归档

协调节点上的 `ax-pilot-pa-retain-calibrations-after-c3-01` 只等原 C3 flat 运行退出。它核对原 systemd invocation、SQLite 和同一正式关闭回执，通过后依次归档 BrewFS 随机读原六轮、混合读写原六轮、C3 flat 六轮。等待时不连接执行节点；没有新的测量、资源或重试。

启动请求为 `/srv/ax-pilot-control/preparation/a-calibration-retention-tools-01/watch.request.json`，SHA256 `7a03c3101581a7a06e1f1ac455228309e629a1a21ce840cce504f359a56c7445`，实际 invocation 为 `86a9f95119ed4a34a88ce87c48cb6758`。只有同目录下的 `run/result.json` 和三份原始包全部通过后，再交给下一项 C4。若 C3 拒绝或任一归档失败，先核实原边界，保留原记录；此进程不会代跑 C4。

归档工具接受 systemd 保留的 `active/exited` 记录，前提是 `RemainAfterExit=yes`、正常退出、无 MainPID。实际 A 组已结束的 C1 服务被正确识别，活跃的 C3 仍会阻止归档。15 项归档相关测试通过。上传阶段用[原 OSS 上传工具](scripts/archive-cloud-to-oss.py)，由协调节点走内网，读回验 SHA256 后才登记下载。
