# 原数据库冷恢复通过，模型调用超时

本机真实 JuiceFS、Redis、RustFS，使用固定的 `opencode-checkpoint-20261111`。源端与重挂后均使用新建的空 HOME、相同的工作区和 XDG 路径。正常卸载后的完整工作区读回、原数据库导出、原会话历史 SHA256 比较均通过；没有额外导入会话。

实际 resume 遇到两次各 180 秒的上游传输超时，OpenCode 自动重试。操作员核对原进程后发出 SIGINT，停止第三次请求。该项结果保留为失败，不算文件系统错误，也不算恢复成功。两次已结束的请求未收到响应正文；第三次在停止时尚未结束，未获得 token 使用量。

[原始结果](result.json)、[空 HOME 与原 XDG 路径](cold-export/started.json)、[超时与停止依据](operator-model-stop-intent.json)、[全部原进程退出及原路径恢复](operator-final-closure.json)、[55 个成员的完整包核验](archive-check.json)。这是本机功能检查，没有跨机或性能结论。

最初的启动参数中，父挂载命名空间字符串为空。独立检查确认实际已由 `unshare --mount --propagation private` 进入不同的命名空间，原目录未被覆盖：[实际命名空间证明](operator-namespace-proof.json)。脚本随后增加空值拒绝检查，原运行代码另存于原始包的 `executed-source.py`，旧结果没有重写。

接口的模型列表请求返回 HTTP 200；旧名称没有列在列表中，不等于已经不可用。DeepSeek [官方说明](https://api-docs.deepseek.com/quick_start/pricing/)明确旧名称 `deepseek-v4-flash` 转到 V4.1 Flash。一次独立的非思考模式小请求已成功：请求旧名称，返回 `deepseek-flash`，共 9 token。此后用新的工作区和 Redis 数据库另做非思考模式验证；不覆盖这次超时结果。
