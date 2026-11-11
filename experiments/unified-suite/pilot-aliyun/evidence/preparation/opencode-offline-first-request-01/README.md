离线接收了原 OpenCode 恢复命令的第一条请求。使用原二进制、完整会话数据库、原工作目录路径、空 HOME 和原工具权限；只把副本中的模型地址及凭据换成回环地址和假凭据。接收端验证假凭据，只返回固定的 HTTP 400。没有模型响应，不计入 C4 的迁移结果。

[原始统计](result.json)显示请求大小为 41,779 B，与两次超时的入站请求大小相同。消息 8 条（12,086 B），工具定义 9 个（29,549 B）；OpenCode 发送 `max_tokens=32000`、`stream=true`，未发送 `max_completion_tokens` 或 `thinking`。消息和工具定义的字节数按紧凑 JSON 计算。原 02 转发器另行注入 `thinking.type=disabled`。离线结果不能判断哪一项导致上游超时。

在 Linux/WSL 中以 root 从仓库根目录运行（仅创建私有命名空间；OpenCode 本身无特权）。需要已归档的检查点、原二进制和原规范路径的父目录。输出使用新路径，避开私有挂载的 `/tmp`、`/run` 和原工作目录父路径：

```bash
python3 -B experiments/unified-suite/pilot-aliyun/scripts/probe-opencode-first-request.py \
  --checkpoint /home/hxy/ax-unified-pilot-prep/session-checkpoint-package-01/checkpoint.tar.gz \
  --checkpoint-sha256 9659f428182c55fbd7d8109bb61d0f37e6ef655f61615746b49baf0d3e5821fe \
  --started experiments/unified-suite/pilot-aliyun/evidence/preparation/session-shared-juicefs-02/native-resume/started.json \
  --started-sha256 be04e08b1c6d973f63be01e7e6c5811d1105d3a46f5898120e9f70134970bebf \
  --out /var/tmp/opencode-offline-first-request-new.json
```

本次执行的[原始脚本](source.py) SHA-256：`5c6b05d1cf8ab2ff05976cfa62da3f766f6c104c1375083b9650e6539458ce25`。
