# C4：JuiceFS 一槽跨机对照

`handoff-1` 固定为一个 Agent、一次迁移、`archive-io-20261026`。原目录保留 BrewFS 与 JuiceFS 各自的 stay 和 move 四个单元。这份草稿只核对 JuiceFS 两个单元需要哪些机器和锁。

| 单元 | 源客户端 | 继续运行的客户端 | 共用存储 | 需启动的 worker |
| --- | --- | --- | --- | --- |
| `juicefs-stay` | A client | A client | A Redis、A RustFS | 3 |
| `juicefs-move` | A client | B client | A Redis、A RustFS | 4 |

两组须从同一检查点分别准备试次。一次 move 内，源端与目标端应打开同一个 JuiceFS 工作区；B 不另建卷，不用 B Redis 或 B RustFS。目前只核对了原清单里的机器、磁盘和地址，未核验实际卷、挂载或续跑。表里的数量不含协调者。

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/draft-c4-juicefs-handoff-route.py
```

命令逐字节核对原 `parallel-control-01` 的计划和清单，并检查所有单元引用的物理资源与清单一致。可用 `--out` 写一个新文件。输出列出两个单元各需的机器和磁盘；同时按现有跨机驱动的加锁方式列出协调端的 20 个锁名。该方式按 A、B 顺序锁住两个完整池，并检查已有 lease。因此即使 B 的 Redis 和 RustFS 不启动，它们仍在锁保护范围内。草稿不取锁，也不分配 lease。

冻结计划中两池都只有一个槽，只支持 C1–C3。给两池加上 C4 后，move 仍要求在一个池内有两个槽；原 `placement` 也不接受目的池字段。已有 JuiceFS 单元分到 B 的规则不能表达「源在 A、目的在 B、存储仍在 A」。草稿因此没有正式计划，不产生可运行单元，也不给出通过结论。

下一步是先审一份独立的 C4 计划，明确两客户端、A 端存储及两池锁；再绑定 JuiceFS 实际卷、检查点和阶段记录，验证写者交接、目标恢复和收尾。继续用原配置和原计划验收 C1–C3。

后续已验证另一条可表达的办法：[新建单逻辑池](C4-SINGLE-POOL-DRAFT.zh.md)，把 A/B 两台客户端各列为一个槽，并用原规划器生成新的四单元草稿。仍须接入正式运行和收尾，不把结果回写旧计划。

协调端现在可用 [`run_guarded`](../../../bench/pilot_handoff_closure_bridge.py) 把迁移、目标挂载核验、卸载与归档放在同一把物理资源锁内。调用方要提供已认领单元的 owner/token、独立审阅的目标请求，以及取回原始归档并核对哈希的回调。失败时保留已认领记录和原始回执，先核状态再决定如何继续。WSL 验证了锁和回执检查；实际卷、双机 SSH 和归档回调仍待验证。
