共享工作区的写回暂存清理修复已完成本地 CI；精确测量 features、真实 FUSE 内容校验及性能对照仍在独立验证，尚不据此批准 #445–#447 的集成 PR。

原缺陷在元数据提交等待期间上传先完成时触发：上传端看到尚未 Committed 而跳过清理，元数据随后发布 Committed 却未接手清理，旧 sealed 暂存记录永久留存。真实 FileWriter 的可控时序测试在原代码上连续四次复现。第一次误用短 exact filter 选中零测试的尝试保留，未计入 RED。

修复在同一 SliceState 锁内决定清理归属。上传尚未完成时发布 Committed，由上传端继续清理；上传已完成时由元数据端先执行既有清理，再发布 Committed。每个 slice 的标记阻止晚到上传端重复提升缓存。原文件系统清理的 best-effort 错误处理保持原样；本轮没有把它改成清理 I/O 出错也保证成功。

正确性提交及待测 release 来源为 `c8f5f16aefe811540782b952481982179a1bfd6f`。提交用的候选 `e2c964c55fafdcced34af58e840ebddda1cf9ea4` 只另修正一个 `cfg(all(test, target_os="linux"))` ignored benchmark 中、计时结束后的验值迭代器，以通过 Rust 1.99 的严格 lint。其余 2,001 个 tracked 条目的 mode/OID 完全相同，包括全部生产源码、Cargo、构建脚本与清理回归；见[等价证明](testfix-release-equivalence.json)。性能产物仍记录实际 `c8f5f16a` 身份，不冒称由 `e2c964c5` 构建。

| 检查范围 | 结果 |
| --- | --- |
| 原代码真实 RED | 同一个测试可执行文件，4 次均在实际残留断言失败。 |
| 最终三项清理回归 | 全通过；同一最终可执行文件再重复三轮，每轮 3/3。 |
| 相关 CBU / writeback cache | 16 通过、1 忽略 / 21 通过。 |
| 默认 features 完整测试 | 1,362 通过，266 忽略。 |
| 默认 features 加 workspace-overlay、observability、hint | 2,858 通过，485 忽略；这是 superset，不等同关闭默认 features 的测量构建。 |
| 官方严格默认 Clippy | `--workspace --all-targets -- -D warnings` 通过。 |
| superset Clippy | all-targets 通过，未加 `-D warnings`；冻结性能候选中的 5 项 workspace warning 在回执逐项保留。 |
| 其余必需门 | 格式、脚本、workspace check/build、两个 FUSE runtime 检查通过。 |

旧失败均保留。第一轮完整 superset 的 native effective export 用例曾报 errno 12，随后同一可执行文件定向 5/5 通过，最终完整套件也通过；Packed 将多种 LimitExceeded 映射为该 errno，因此不能仅据日志归为系统 OOM，也没有建立它与清理补丁的因果关系。严格 Clippy 的原失败及其独立测试修复同样保留，没有放宽门槛。

[ci-final.json](ci-final.json)列每条命令、时间、原始日志哈希、feature 范围和失败记录。[独立终审](independent-final-ci-review.json)与[协调者复核](coordinator-review.json)均核对 107 个归档成员及 23 份完成门的日志；协调者直接重算默认/superset 通过数，并确认四次实际 RED。证据包 [evidence.tar.gz](evidence.tar.gz) 的 SHA-256 为 `e05cf686f84dcd2961f671c52adfdaf46112a719580774b42f807c514b1eadaf`，逐文件清单为 [artifact-sha256.json](artifact-sha256.json)。

后续对照使用同时包含相同正确性修复的基线与候选，原三项性能改动的整树差异单独核验。组件回归通过不替代生产 SSD overlay 路径、正常冷重挂及远端内容验证。
