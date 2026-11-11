# 小型流式请求检查

2026-11-11 21:32:31，使用原 DeepSeek 配置发出一次流式请求，输出上限 16 token。0.699 秒收到完整 SSE，HTTP 200，返回内容符合 `OK`，用量为 8 个输入、1 个输出 token。[实际结果](session-stream-probe-01.json)没有保存密钥或响应正文。

这只证明该小请求的流式接口可用。完整 OpenCode 续跑仍需另行通过；完整请求结构见 [离线检查](opencode-offline-first-request-01/README.md)。

通用探针已增加对应参数；以下命令会实际发送一次请求，输出路径须为新文件：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/probe-session-provider.py \
  --provider-config /mnt/c/Users/HXY/.config/opencode/opencode.json \
  --stream-generation-smoke --out /path/to/new-result.json
```
