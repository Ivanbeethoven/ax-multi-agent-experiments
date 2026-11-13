# 开测前必须检查 RustFS 文件句柄上限

**RustFS 的 `nofile` 软、硬上限统一设为 `1048576`。逐台检查容器内实际 RustFS 进程；不能只看登录终端的 `ulimit -n`，也不能只看配置文件。**

## 本次发现

2026-11-13，C1 的 64 MiB 随机混合读写完成，但冷重挂读取失败。独立复现 `ax-c1fix-806bc64ae793` 使用相同 BrewFS `8a215a65`、RustFS `f4ed2025` 和 fio 参数，再次在冷重挂校验阶段退出 1。RustFS 日志明确出现 `Too many open files (os error 24)`，HTTP 接收连接也因此失败。

四个 RustFS 进程的 `/proc/<PID>/limits` 均为：软上限 **1024**，硬上限 **524288**。这证明本轮存在实际句柄耗尽，不能把后续的 `EIO` 或 S3 `InternalError` 直接判成 BrewFS 数据损坏。原始 C1 记录见[失败分析](../../experiments/idcai-six-node-20261113/C1-FAILURE.zh.md)。

**当前只完成文档修正。部署脚本尚未加入以下配置和自动检查，调高后的负载复验也尚未执行。执行 Agent 必须先落实配置并实查，再开测；不要据本文把失败项标成已修复。**

## 部署时显式配置

Docker 的 RustFS 启动命令增加：

```bash
--ulimit nofile=1048576:1048576
```

Compose 在 RustFS 服务下增加：

```yaml
ulimits:
  nofile:
    soft: 1048576
    hard: 1048576
```

如果外层使用 systemd，服务同时设置 `LimitNOFILE=1048576`；`systemd-run` 对应参数为 `--property=LimitNOFILE=1048576`。Docker 场景仍须显式传入容器的 `--ulimit`，不能用外层设置代替。若主机或运行平台拒绝这个值，先解决限制，不沿用 1024 开测。

## 启动后逐节点核验

设置实际容器名，保存下列输出到本轮记录：

```bash
CONTAINER=ax-four-实际运行编号
PID=$(docker inspect -f '{{.State.Pid}}' "$CONTAINER")
test "$PID" -gt 1
awk '/^Max open files/ {
  found=1; print
  if ($4 != 1048576 || $5 != 1048576) exit 1
} END {if (!found) exit 2}' "/proc/$PID/limits"
```

任一节点没有运行、限制不符或无法读取，都不能开始负载。`health/ready=200` 只能说明服务就绪，不能代替这项检查。运行期间保存 PID、进程启动时间、句柄数量、软硬上限和 RustFS 错误日志；不能只在测试结束后数句柄，因为峰值可能已经消失。

## 配置改好后怎么复验

保持原二进制、64 MiB 文件、4 KiB 操作、75:25 读写比、每次写后 fsync 和随机种子 9173。重新执行准备、混合读写、排空、卸载及全量冷重挂校验，检查是否还有 `EMFILE`、S3 错误和读取失败。通过后再重复，观察句柄峰值及结束后的占用，不能只凭一次“能挂载”结束。

所有对照使用相同上限；修正配置后另建批次，保留旧失败和版本记录。本轮结果及日志先留在本地和服务器，不上传新的原始数据。
