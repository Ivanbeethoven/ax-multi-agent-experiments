# 其他会话的工作范围

2026-11-12 只读核对了另外两个活动会话，未修改它们的源码、构建缓存或结果。

- 热 lower Overlay：独立分支 `codex/hot-lower-overlay-cache-impl-20261112`，在做上层查询缓存、失效和优化构建验证。
- Packed 预取：rbm 的 `/home/dev/ax-packed-brewfs`，分支 `codex/packed-prefetch`，核对时 HEAD `65bc08e2d3dc447dcda4f73ce1c3ab096722f11d`。同时处理并发冷读、行缓存预算、写入区间扫描及统计计数。

后一个分支把 `workspace_overlay/packed_v3/wire005` 改名为 `workspace/packed/snapshot`。实际检查其 `native_placement.rs`，导入的 `_root_workspace` 仍持有到函数返回，lower 不存在的 parent 仍返回 `NotFound`；本轮两个正确性故障未被该分支解决。

本轮继续用单独的 ca07159 诊断候选验证脚本，不接管它们的性能优化，不往 BrewFS 主线推送候选。以后合并时按新目录移植对应方法，不能直接覆盖另一个分支。所有使用 rbm 的重编译和新增负载串行取得 `/home/dev/.ax-rbm-heavy.lock`。
