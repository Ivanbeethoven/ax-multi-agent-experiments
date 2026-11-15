Pool-b三台新私网主机完成身份、系统盘排除和新数据盘空盘检查后，格式化6块指定数据盘；所有原始命令、实际退出、盘serial和包安装日志保留在各机`/root/ax-pool-b-bootstrap-01/`。

Redis为`10.20.2.169:6379`，RustFS为`10.20.2.170:9000`。每池独立盘、Redis、RustFS和collector；共用原跳板的代理、资产服务和registry。客户端跨机PING与S3只读空桶检查通过，原生指标带独立pool-b标记。

新BrewFS安装到`/srv/ax-client/build-ca07159-01`，旧assets保留；版本、程序SHA、镜像digest、PID、四个layout开关、盘身份和限额见`result.json`。凭据只在私有env文件中，未入Git。

一次对象部署在身份模块导入处失败，未改服务或磁盘。失败日志保留，修复后以新的显式步骤成功；新增入口回归，22项测试通过。

这是服务就绪记录。100k输入准备、C3原生导入、正式测量和监控开销资格各自留独立记录。

后续准备已通过：`input-normal-exit.json`为C3/C4各100k源与原unit正常退出；`c3-preparation.json`为JFS原生100k完整导入、每文件fsync、原cold全量409.6MB校验和两次正常卸载；`c1-qualification.json`为6次64MiB CRC、两次正常卸载，以及DB62小规模clone/父子隔离/同卷重开资格。它们均为准备与功能检查，不是正式性能样本。C3完成后先采纯C3容量after，再开始C1与clone，容量证据由协调者另存。

05:16:25Z准备收尾时，客户端slot空，三处准备挂载均已消失，8910/9567/19567无监听；Redis、RustFS及collector保留供后续测量。此后槽位交回协调者。
