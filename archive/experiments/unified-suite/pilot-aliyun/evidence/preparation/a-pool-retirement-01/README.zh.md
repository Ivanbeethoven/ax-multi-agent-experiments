# A 池备份与释放

本轮实验已暂停。2026-11-12 12:52:14 UTC 的[独立云 API 核验](final-audit/result.json)确认：原 A 池三个实例及九块盘均已不存在，jump 及两块盘保留，jump 为 StopCharging。本次只清理用户授权且有创建证明的资源，没有补跑失败项。

## 删除范围

只处理 [创建回执已核对](../cleanup-a-ownership-01/README.zh.md) 的三个实例和九块盘，共 520 GiB。释放脚本再次比对实例 ID、创建时间、完整附属盘集合及 `DeleteWithInstance`，不按 `ax-pilot-*` 名称批量删除。

| 角色 | 实例 ID | 随实例释放的盘 |
|---|---|---|
| client | `i-bp1809mw7rlujtj9f705` | 40 GiB 系统盘、100 GiB 数据盘 |
| metadata | `i-bp1gy3cfuj9jik0y4oc3` | 40 GiB 系统盘、60 GiB 数据盘 |
| objects | `i-bp1h9kaoya0predubdkn` | 40 GiB 系统盘、4 × 60 GiB 数据盘 |

保留 `ax-c1-jump`（`i-bp1cp8ccqor34u2bo5os`）、其 40 GiB 系统盘和 100 GiB 协调盘，以及原 EIP、网络、密钥和 OSS。任何没有本任务创建证明的资源都不处理。

## 备份内容

[备份索引](backup-index.json)记录包名、SHA、文件数、实际脚本版本及两个私有位置。三个包共 4,397,553,768 B，原内容 20,848,223,877 B。包内有逐文件 SHA 清单；client 的 43 个符号链接保存为清单中的链接目标和属性，不跟随读取。

| 包 | 压缩字节 | 原文件数 | 内容 |
|---|---:|---:|---|
| metadata | 307,741,105 | 235,047 | Redis AOF/RDB、配置、原始观测、运行与失败记录 |
| client | 2,520,611,826 | 409,780 | trace 归档与解包内容、回放输出、运行二进制、脚本、配置和日志 |
| objects | 1,569,200,837 | 82,058 | 原始观测、版本和部署记录、C2 三个诊断桶、C4 会话检查点及选定小桶的四盘原件 |

三个源包和 jump 副本均逐文件验证了 SHA。metadata、objects 还完成了本机副本核验。用户随后明确只要结果，client 原始包下载已取消，未生成本地完整原件；释放前重新核对了 jump 完整包的 SHA，见 [client 释放范围](client-release-preflight.json)。原件含内部配置和会话，继续保存在 jump 私有目录，GitHub 只放可公开回执。

本地交付目录为 `C:/Users/HXY/Documents/AX-experiment-results/2026-11-12`，只含 [结果表](../../../../current/results-20261112/RESULT.zh.md)、关键指标和来源索引，约 51 KB，不含 trace、完整监控日志或运行产物包。

客户端排除四棵可重建的 C3/C4 合成输入树，保留生成程序、参数和完整校验记录，见 [精确范围](client-classification.json)。对象端淘汰其余旧实验对象库，见 [逐桶范围](objects-selection-summary.json)和[口径更正](objects-discard-clarification.json)。这些包用于保存结果和定位问题；恢复实验要新建服务、重新导入输入，不能拿选定对象包直接还原完整 RustFS。

## 脚本与未完成项

- [冷备份](../../../scripts/preserve-a-backend.py)：核对实例和盘，只读挂载，记录分类，打包并逐文件核验。
- [精确释放](../../../scripts/release-owned-a.ps1)：核对创建证明、三份包校验和当前附属盘；默认只查，`-Execute` 才发单次非强制删除请求。
- [删除后复核](../../../scripts/audit-retired-pool-a.ps1)：只查固定 ID，确认三台九盘消失、jump 和两块盘保留且为停机不收费。
- [运行脚本入口](../../../scripts/README.zh.md)和[C2–C4 未完成清单](../../../../current/PENDING.zh.md)。最后 C2 发布在 300 秒超时，没有进入完整 trace 回放；失败记录保持原样，不计为通过。
