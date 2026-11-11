2026-11-11 22:21:44，用[已固定脚本](../../../scripts/probe-opencode-upstream-once.py)发送了一次完整恢复请求。脚本 SHA256 为 `de62af12318de51c4bae9a5612ba28c9e9c80e7feab3b942e7630b0598f4de6e`；命令沿用[预检说明](../opencode-upstream-once-prep-01/README.md)。[原始回执](result.json)保留实际统计，没有请求或响应正文、地址和密钥。

完整会话产生 8 条消息、9 个工具定义。设置 `max_tokens=512`，按原 relay 02 注入 `thinking=disabled` 后，请求为 42,221 B。HTTP 200，首个 SSE 0.238 秒，完整传输 1.281 秒；返回 1 个工具调用并正常结束。用量为 9,599 输入、81 输出，共 9,680 token。无重试或跳转。

OpenCode 在隔离环境中生成请求后已收到本地固定 400 并退出；真实模型回复没有交回 OpenCode，未执行工具。此次证明完整负载可以通过该传输路径获得响应，不能计为原生恢复或 C4 通过。传输采用 curl、原实验采用 urllib，且时间不同，不能据此认定 512 就是修复原超时的原因。下一步仍需用原 relay 和工作区做一次真实原生恢复。
