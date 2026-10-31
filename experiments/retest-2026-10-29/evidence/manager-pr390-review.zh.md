# Manager #390：fork 重试与删除安全性审查

日期：2026-10-31。PR：[brewfs/brewfs#390](https://github.com/brewfs/brewfs/pull/390)，已合并。独立审查固定在合并提交 `1e7a95c10cbff8d34faf768ebd621ae8261cbd91`，PR head 为 `2396dd65fb8e64c1ea6cc1c6f11321503bca88ce`。本次不修改产品代码，不部署服务或启动实验。

## 结论与适用范围

源码审查未发现需要阻断该 SQLite MVP 的明确 fork 重复创建或删除共享 lower 缺陷。结论仅适用于配置的可信本地 catalog、单 Manager、`workspace-v1` 范围；不构成 Redis/TiKV Manager、真实挂载、远端持久性、两节点交接或规模性能验收。

审查时 `origin/main` 已前进到 `060db0df089f916609ce55445db44a4876c40c4c`（#386），涉及 `upper_bucket` 的 schema/调用变化。本次测试不冒充该后续版本的回归结果。下一批应重新冻结 actual source/binary SHA 并做相应资格。

## 重点检查

| 风险 | 核对结果与源码 |
|---|---|
| 相同 fork 重试重复创建 | [Manager admission/replay](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/src/manager/mod.rs#L534) 持久化作用域、key、请求摘要和预分配目标 ID；同请求复用 operation，改请求冲突。重试不分配另一套有效资源 |
| 部分成功或 catalog 提交后丢响应 | [执行与恢复](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/src/manager/mod.rs#L903) 保留已完成项，未完成项按原 ID 对账；[共享 admin](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/src/workspace_overlay/admin.rs#L134) 核 owner、Active、head epoch/ID、精确 revision，不能把已推进 workspace 重置成初态 |
| 恢复误落另一个 catalog | [绑定回归](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/src/manager/binding_tests.rs#L118) 覆盖 pending fork/delete 遇到卷重绑定；操作绑定 catalog 引用、路径指纹与真实卷 UUID。丢失原 catalog 不自动新建替身 |
| 删除检查与 lease 竞争 | [SQLite 删除事务](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/src/workspace_overlay/stores/database.rs#L3287) 在写事务内检查 Active 且未过期的 lease 后才写删除标记；[BEGIN IMMEDIATE](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/src/workspace_overlay/stores/database.rs#L1233) 获取跨连接写锁，不仅依赖进程内 mutex |
| 自动强删或误删共享数据 | Manager 调用 [mark_workspace_deleting](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/src/workspace_overlay/admin.rs#L196) 时不强制 fencing；HTTP 拒绝删除 body/query。只标 workspace/可写 head，shared lower 不变；回复 `physical_reclaimed=false`。子 workspace/snapshot 引用保护物理回收，不禁止父 workspace 的逻辑删除 |
| 删除响应丢失后重复操作 | admin 对已是 Deleting 的资源返回幂等成功；同删除 key 仍对应原 operation。有效 lease 导致失败后可在原因解除时显式重试原请求，不能改成 force |
| 空卷冒充已有数据、非法接入 | 初始化与登记分开；空 namespace 检查在事务内，register 检查真实 header/schema，拒绝 packed 绑定。旧 Console 配置登记不代表真实卷。管理员 token 的范围是全部配置 catalog，owner 不是租户边界 |

## 独立验证

合并提交的独立 checkout 保持无改动。首次重编译因 WSL 23 GiB 内存且无 swap 被 OOM kill；增加临时 12 GiB swap、改为单编译 job 后，第二次编译又随 WSL 退出而中断，原因未进一步确定。两次都没有产生测试结果；临时 swap 已关闭并删除，未修改 WSL 持久配置。**本次未完成合并提交的独立重新构建。**

随后复用实现 Agent 保全的 `merge-feature-full-libtest`，重新核 SHA256 `9402f56ae207e301070cde9b04d09f68c564e256e3fd61670fa2b523ae0fa73a`（1,518,169,704 字节）。保存的构建源码补丁 SHA 为 `425e30ae9f1621ef3a69c63319cc3680b25e00f6f7c31aec1e4dcc79e049dbfd`；在临时 Git index 上从记录的基线重建，得到 tree `4c2c18538d2ac3e4fa1b5ac943a78aacaf16a0ea`，与 PR head `2396dd65` 一致。另核 Manager、Console、共享 admin、catalog、SQLite store、lifecycle 及根 Cargo 清单/lockfile 从该 head 到合并提交均无变化。合并提交含额外 packed/FUSE 改动，不能将旧产物称为整个合并版本的构建产物。

在 Ubuntu 24.04（WSL）中以 `RUST_TEST_THREADS=2` 对该已核验产物独立复跑以下 **40 项，全部通过**。作者构建记录为 Rust/Cargo 1.99.0、`fuse-tokio-runtime,workspace-overlay`；本次复跑没有启动 Redis/TiKV/S3/FUSE。机器可读的 [来源与结果](manager-pr390-review-tests.json) 记录源/产物/日志 SHA 与实际时间。

| 过滤组 | 本次结果 |
|---|---|
| `manager::` | [19 PASS](manager-pr390-tests-manager.txt)，含并发同 key、部分失败、丢响应/重启、卷重绑定、lease 阻止删除 |
| `workspace_overlay::admin::tests::` | [6 PASS](manager-pr390-tests-admin.txt)，含精确 head/revision、推进后拒绝恢复、共享 lower/子 workspace/snapshot 保护 |
| `console::server::tests::manager_` | [6 PASS](manager-pr390-tests-http.txt)，含鉴权、请求校验、真实 catalog HTTP fork/delete 与幂等 |
| `workspace_overlay::stores::database::manager_init_tests::` | [9 PASS](manager-pr390-tests-catalog.txt)，含非空/外来 schema、缺失文件和 packed 历史拒绝 |

本次每组实际执行形式（VERIFIED_LIBTEST 是上述已核 SHA 的产物，FILTER 为上表值）：

```bash
env RUST_TEST_THREADS=2 "$VERIFIED_LIBTEST" "$FILTER" --nocapture
```

需要独立构建时，在冻结源码上用 `cargo test --locked --offline --no-default-features --features fuse-tokio-runtime,workspace-overlay --lib "$FILTER"`；本次尝试未完成，不能记为 PASS。PR 作者报告的全量 feature/default、Operator、真实 Redis/TiKV 和 CI 结果属于作者验证，本次不计为独立复跑；Redis/TiKV 的核心契约测试不代表 Manager 接口支持这些后端。当前不接受或更新任何性能数字。

## C1/C4 接入处置

[逐步接入手册](../runbook/MANAGER.zh.md) 固定请求/key/operation、逐子项完成、失败恢复、删除前保全与卸载门。已将 C1/C4 prompts、F/M 与规模入口同步到实际能力：#390 记为 `local_api_only`，共享后端/格式未匹配时保留原生路径。单次 fork 上限 100，128 须预先拆批并报告共同完成与排队；未实现的 checkpoint/drain/handoff 不出现在可执行指令中。实验保持未启动。
