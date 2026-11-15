# 冻结试跑下载索引

截至 11 月 11 日 18:48，已验收的 13 项正式测量均有公开原档。表中每项包含客户端、元数据和对象存储三类记录；部分结果把三类记录装在一个包里。下载清单含公开 URL、字节数和 SHA256。

| 实验 | 文件系统 | 正式原档 | 原校准 |
|---|---|---|---|
| C1 随机读 | BrewFS flat | [下载清单](../../c1-native-randread-02/oss/index.json) | 已公开 |
| C1 随机混合读写 | BrewFS flat | [下载清单](../../c1-native-randrw-01/oss/index.json) | 已公开 |
| C3 10 万小文件 | BrewFS flat | [下载清单](../../c3-flat-01/oss/index.json) | 已公开 |
| C3 10 万小文件 | JuiceFS | [下载清单](../../c3-jfs-01/oss/index.json) | 已公开 |
| C4 10 万文件派生 | JuiceFS | [下载清单](../../c4-jfs-01/oss/index.json) | 六轮已公开 |
| C2 完整 trace | BrewFS flat | [下载清单](../../cloud-20261111/05357b5089cd679c-r1-native/oss/index.json) | 已公开 |
| C1 顺序读 | JuiceFS | [下载清单](../../cloud-20261111/34d56abaf85454a2-r1-juicefs/oss/index.json) | 待补收 |
| C1 顺序读 | BrewFS flat | [下载清单](../../cloud-20261111/34d56abaf85454a2-r1-native/oss/index.json) | 待补收 |
| C1 随机读 | JuiceFS | [下载清单](../../cloud-20261111/451ef1d28b5d875c-r1-juicefs/oss/index.json) | 待补收 |
| C1 顺序写 | JuiceFS | [下载清单](../../cloud-20261111/5ca7353ba24b2a18-r1-juicefs/oss/index.json) | 待补收 |
| C1 顺序写 | BrewFS flat | [下载清单](../../cloud-20261111/5ca7353ba24b2a18-r1-native/oss/index.json) | 待补收 |
| C1 随机写 | JuiceFS | [下载清单](../../cloud-20261111/7084af367e0bcfad-r1-juicefs/oss/index.json) | 已公开 |
| C1 随机写 | BrewFS flat | [下载清单](../../cloud-20261111/7084af367e0bcfad-r1-native/oss/index.json) | 已公开 |

历史 C1 的五组原校准仍需从 A 组补收。补收不会重新测量。C4 表中的结果只覆盖工作区派生，跨机迁移尚无正式验收结果。

查看[逐项校验回执](public-formal-coverage.json)或[历史 C1 补档步骤](../../../OLD-C1-ARCHIVE.zh.md)。
