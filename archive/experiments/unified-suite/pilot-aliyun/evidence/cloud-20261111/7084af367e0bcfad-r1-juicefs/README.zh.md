# JuiceFS随机写：正式完成

单元`7084af367e0bcfad-r1-juicefs`。原code-07、JuiceFS 1.4.2，与BrewFS 622736b使用同一冻结配对。64MiB工作量、4KiB随机写、每次写后fsync、1个Agent、1次正式重复。

| 指标 | BrewFS | JuiceFS |
|---|---:|---:|
| 工作量共同wall | 1780.247s | 221.380s |
| 写IOPS | 9.205 | 74.122 |
| write p99 | 0.465ms | 12.517ms |
| fsync p99 | 109.576ms | 108.528ms |

write耗时与随后fsync耗时分开统计。该设置下BrewFS整体较慢，原因尚待原始指标分析，不能只比较write p99。

六次校准均通过，监控开销配对中位数为0.409%，低于5%门槛。正式负载、CRC冷读回、上传排空、真实退出和原始采集归档均通过；参见本目录的sample、closure、observer-overhead及[统一报告](../c1-progress-04.json)。

原始包已独立下载到本机缓存`C:/Users/HXY/.cache/ax-pilot-20261111/retained-juicefs-randwrite-01/`，没有提交大文件到Git：

- `calibrations.tar.gz`：22,549,681B，SHA256 `b6dbfe8101fcad8695217bfa9c2a5dc1fe28556a340a1a54197cd9197bcc824f`；13个外层及嵌套包、17,703个成员全部核验。
- `formal/cell.tar.gz`：15,468,007B，SHA256 `780c644bdac0ecf0ac317091fe89c98229537c28ffc1287c2f437d4ac0169c39`；4个外层及嵌套包、11,557个成员全部核验。

本目录的两份`nested-check.independent.json`保留独立核验结果。该pilot只有一个正式样本。
