# AX / BrewFS 实验结果 · 2026-11-12

| 实验 | 已验证 | 未完成及主要困难 |
|---|---|---|
| C1 同步混合读写 | 同一份 64 MiB 输入，4 KiB 随机读写，每次写入后同步。先 JuiceFS 后 BrewFS：BrewFS **36.160 秒**，JuiceFS **792.007 秒**；反序：BrewFS **63.425 秒**，JuiceFS **741.537 秒**。四臂均通过冷重挂全量 CRC。[双轮核验](evidence/c1/cross-order-comparison.json) | 每种顺序只跑一轮，两轮使用不同物理盘，观测开销未标定。这是该 I/O 诊断的结果，不能归因到单个设计或外推为 Agent 收益。JuiceFS 混合读写的正式项按用户要求跳过。 |
| C2 完整 trace 与 Overlay | 同一 BrewFS 引擎在同样的两个热文件上做只读对照：两组各 128 轮，计时阶段对象存储读取均为零；查询上层 **1.329 秒**，跳过上层 **0.981 秒**。[热读摘要](evidence/c2/hot-lower-summary.json) | 云端通过 RustFS 就绪、查桶和建桶检查。Packed 发布 **300.001 秒**超时，留下 **145 个对象、24,520,277 B**，没有最终清单，未进入回放。发布等待尚未定位；缺完整回放、冷验和可写 Overlay 对照。[发布回执](evidence/c2/cloud-publisher-receipt.json) |
| C3 共享小文件 | 云上用旧十万文件基线中的 **64 个文件**跑完四阶段，共 **128 次**内容校验，正常卸载。[四阶段结果](evidence/c3/worker-cohort-result.json) | 独立的一万文件输入已在本地全量核验，尚未做云上挂载、阶段测试或系统对照。现有抽样不能回答全量性能。[一万文件清单](evidence/c3/tenk-input-set.json) |
| C4 原生派生 | BrewFS 候选版原生 fork、JuiceFS 原生 clone 使用同样两个文件，均通过子读父、子修改后父不变、空缓存重挂读回。[相同输入](evidence/c4/same-input-and-versions.json) · [BrewFS](evidence/c4/brewfs-fork-result.json) · [JuiceFS](evidence/c4/juicefs-clone-result.json) | 一万文件的配对计时和 BrewFS 正式产物资格尚未完成。 |
| C4 跨机恢复 | JuiceFS A→B 冷重挂读回同一工作区 **894 项**。空 HOME 下 OpenCode 导出与源端同为 **10,734 B**，SHA-256 一致。[跨机回执](evidence/c4/cross-machine-closeout.json) | 整项未通过：目标主进程退出后仍有后台进程，超过 5 秒门限，未采目标零上传债务。退出等待已在本地修复，尚未云端复验；未调用模型继续任务。[目标退出](evidence/c4/cross-machine-target-native-exit.json) · [本地修复](evidence/c4/local-exit-closure-fix.json) |

实验已暂停。以上是小试和诊断结果；历史账本的 14/28 不用于计算当前完成率。此目录只含结果和关键 JSON 回执。[来源索引](source-index.json) · [SHA-256 清单](SHA256SUMS)。
