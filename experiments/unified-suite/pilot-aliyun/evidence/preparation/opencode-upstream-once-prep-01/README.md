这个入口诊断完整 C4 恢复请求在哪里停住。原两次恢复在 180 秒内都未得到 HTTP 状态码；此前通过的 16 token 小请求不能回答这个问题。

[离线预检](offline-capture.json)使用原 OpenCode、完整历史、原命令和工具权限。只加输出上限 512；首请求 41,777 B，按原 relay 02 加 `thinking.type=disabled` 后是 42,221 B。OpenCode 只有 loopback，收到固定 HTTP 400 后退出。请求通过匿名内存文件传递；预检未调用上游。另在独立 loopback 网络用本地 TLS 服务和 CONNECT 代理验证了单次 POST、307 不跳转、SSE 计数和内容过滤。

实际诊断用 [probe-opencode-upstream-once.py](../../../scripts/probe-opencode-upstream-once.py)。脚本 SHA-256：`de62af12318de51c4bae9a5612ba28c9e9c80e7feab3b942e7630b0598f4de6e`。Linux/WSL 以 root 在仓库根目录运行：

```bash
python3 -B experiments/unified-suite/pilot-aliyun/scripts/probe-opencode-upstream-once.py \
  --checkpoint /home/hxy/ax-unified-pilot-prep/session-checkpoint-package-01/checkpoint.tar.gz \
  --checkpoint-sha256 9659f428182c55fbd7d8109bb61d0f37e6ef655f61615746b49baf0d3e5821fe \
  --started experiments/unified-suite/pilot-aliyun/evidence/preparation/session-shared-juicefs-02/native-resume/started.json \
  --started-sha256 be04e08b1c6d973f63be01e7e6c5811d1105d3a46f5898120e9f70134970bebf \
  --out /home/hxy/ax-unified-pilot-prep/opencode-upstream-once-512-01.json \
  --execute-once --provider-config "$OPENCODE_PROVIDER_CONFIG"
```

省略最后两个选项就是零上游预检，使用另一个新输出路径。真实模式只接受原配置的 SHA，只发一次，无重试或跳转。使用当前环境下 urllib 选择的 CONNECT 或直连路径，但实际传输是 curl HTTP/1.1；有代理时 DNS/TCP 计时指代理端。连接阶段上限 8 秒，总上限 45 秒。回执记录上传量、响应头及首个 SSE 的时间、HTTP 状态和 SSE 统计，无请求正文、响应内容、地址或凭据。诊断能区分失败阶段；它不能证明 512 是原故障原因，也不验证模型继续执行或 C4 通过。
