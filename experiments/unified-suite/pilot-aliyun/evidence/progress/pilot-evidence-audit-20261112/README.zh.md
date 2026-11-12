# 小规模实验：输入、账单与资源回执

核查于 2026-11-12，范围为历史七份 trace 和本次阿里云 C1–C4 pilot 专属资源。原冻结方案正式验收仍为 14/28；新的一万文件方案和新 C1–C4 结果另按各自资格记录。

| 要核对的事 | 核查结果与证据 |
|---|---|
| 七份 trace 是否回到原路径 | [原迁移清单](../../../../../../scenarios/k8s-containers/validation/drive-migration-2026-11-10.json)、[上传回执](../../../../../../scenarios/k8s-containers/validation/historical-oss-restored/restoration.json)和[匿名复查](../../../../../../scenarios/k8s-containers/validation/historical-oss-restored/public-readback-20261112.json)逐对象一致：28 个对象、10,480,588,662 B，路径、字节数、原始 SHA、OSS 元数据 SHA、上传前和 OSS 服务端 CRC64 均对应。7 个大归档的匿名 Range 请求均成功。 |
| 七份原清单和下载说明是否对应 | [新核查回执](../../../../../../scenarios/k8s-containers/validation/historical-oss-restored/manifest-audit-20261112.json)：4 份独立原清单已从 OSS 全文读回；本次只匿名完整下载 889,165 B 的 metadata.tar.gz 并核 SHA，从中读取另 3 份原清单，逐条核对压缩包和文件清单的大小及 SHA。 [数据入口](../../../../../../scenarios/k8s-containers/data-catalog.md)与原路径一致。 |
| 大包是否完整下载并复算 SHA | 本次 0/7。原始文件已算 SHA；复查看到的是 OSS 的 SHA 元数据、CRC64 和 1 字节响应。将来在杭州已有运行节点首次下载时，用现有下载器加 --internal 流式校验并保存 7 条输出，不必单独开机，也不必走公网重复下载约 9.69 GiB。 |
| 资源是否真正释放 | [独立云 API](../../preparation/b-pool-retirement-01/independent-audit.json)确认原 B 三台及九盘已不存在，A 三台及原跳板停机不收计算费。C1 四组临时盘均已释放：[顺序 01](../../preparation/c1-b-randrw-scratch-01/released.json)、[顺序 02](../../preparation/c1-b-randrw-scratch-02/released.json)、[反序 01](../../preparation/c1-b-randrw-scratch-reverse-01/released.json)、[反序 02](../../preparation/c1-b-randrw-scratch-reverse-02/released.json)；[On1 专用盘](../../preparation/c4-on1-scratch-retirement-01/independent-closure.json)也已删除。A 池与协调盘仍保留，其磁盘费用会继续发生。 |

[billing-daily.json](billing-daily.json)覆盖 42 个专属资源 ID，保留 11 月 11–12 日 68 次 ID × 日期查询、79 条计费项、请求号与查询时刻。用阿里云 [DescribeInstanceBill](https://help.aliyun.com/zh/user-center/developer-reference/api-bssopenapi-2017-12-14-describeinstancebill/) 的按日、按计费项、税前应付 PretaxAmount 汇总。每次只查询一个实验资源 ID，响应按同一 ID 和日期筛验；没有查账户总账。空响应不等于免费：六块系统盘单独查没有记录，已在所属实例的 SystemDiskSize 项计入一次。On1 专用盘在 11 月 11 日没有返回计费行，不能写成当日零费用。另核 [A 三台、其九盘和协调盘的创建日](billing-creation-day-check.json)：UTC 11 月 10 日 18:48–18:51，即北京时间 11 月 11 日 02:48–02:51；按相同 13 个 ID 补查 11 月 10 日账单，均无返回行。原 B 三台和其盘在 UTC 11 月 11 日才创建；既有跳板早于本批次，未计入专属小计。

| 账单日期 | 节点计算 | 系统盘 | 数据盘和协调盘 | 临时盘 | 当时已出账 |
|---|---:|---:|---:|---:|---:|
| 11-11 | ¥107.77 | ¥4.10 | ¥29.58 | 未见计费行 | **¥141.45** |
| 11-12 | ¥32.73 | ¥4.28 | ¥30.35 | ¥2.39 | **¥69.74** |
| 合计 | **¥140.50** | **¥8.39** | **¥59.92** | **已见 ¥2.39** | **¥211.19** |

分项按分显示；合计用原值相加。11 月 12 日仍可能补入计费行。共享跳板、EIP 与公网流量没有计入实验专属小计；OSS 存储、请求和流量需按指定桶查[分账明细](https://help.aliyun.com/zh/oss/how-to-view-the-billing-of-a-bucket)，共享桶的分账仍不能直接拆到实验前缀。当前未取得最终结算和实付回执。最初五台跑一次 pilot 的 ¥20–35 是估算；后续用户明确可不设预算上限，仍要求节省；实际已出账金额超过最初估算。此前报价乘运行时间的历史估算不与这份实际出账相加。
