给同一 OpenCode 会话副本的模型配置加 `limit: {"context": 0, "output": 512}`。这版的配置要求同时填写 `context` 和 `output`；原自定义模型的有效 `context` 已是 0。[配置依据及版本记录](provenance.json)来自固定二进制和检查点里的 SDK。

与[默认上限探针](../opencode-offline-first-request-01/result.json)相比，[这次结果](result.json)中的 `max_tokens` 从 32,000 变成 512，请求从 41,779 B 变成 41,777 B。完整历史、8 条消息和 9 个工具定义的数量和字节数，以及其他已记录字段都一致。只发了一次回环请求，收到固定 HTTP 400，零上游请求，退出后无残留进程。这只验证参数生效，不能解释上游超时，也不计入正式 C4。

沿用[原探针的复现命令](../opencode-offline-first-request-01/README.md)，增加 `--output-limit 512`，并把输出改为新路径（例如 `--out /var/tmp/opencode-offline-output512-new.json`）。[本次执行的原始脚本](source.py)与当前可复用脚本一致；SHA-256 见 [provenance.json](provenance.json)。
