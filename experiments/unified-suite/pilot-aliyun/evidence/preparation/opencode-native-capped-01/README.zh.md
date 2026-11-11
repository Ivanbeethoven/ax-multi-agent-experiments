# OpenCode 从工作区恢复：本机真实调用已通过

2026-11-11 22:58（北京时间），同一 WSL 主机上的 JuiceFS 工作区完成一次真实原生恢复。沿用原 OpenCode 二进制、会话 ID、完整历史和任务；HOME 从空目录开始，数据库及 XDG 状态位于工作区内，没有导入会话 JSON。

实际发生 2 次模型请求，完成 1 次新工具操作。旧 4 条消息保持原顺序和内容，`final.txt` 的 SHA256 与任务开始前固定的预期一致。卸载并重新挂载后，完整工作区字节、原生会话导出、SQLite 中的新工具记录再次通过核验。3 次 FUSE 卸载正常结束。

| 检查 | 证据 |
|---|---|
| 原任务、历史、工具、最终文件和正常退出 | [运行结果](receipts/result.json)、[原生恢复结果](receipts/native-resume/result.json)、[外层退出](receipts/outer-exit.json) |
| 两次请求，无自动重试，响应流完整结束 | [调用回执](receipts/relay-calls.jsonl) |
| 源挂载、恢复后挂载、最终冷读挂载正常退出 | [源](receipts/source-closed.json)、[恢复后](receipts/resumed-closed.json)、[最终冷读](receipts/final-closed.json) |
| 原始文件 SHA、13 份公开回执与私有原文的对应关系 | [文件清单](source-manifest.json) |
| 独立只读复核 | [audit.json](audit.json)、[复核脚本](audit-local.py) |

独立复核重新比较了原生事件、前后导出、旧消息指纹和预期文件 SHA。运行当时的正常退出依据是三份 `*-closed.json` 和相应命令 `exit.json`，不是稍后检查时 PID 已消失。SQLite 和完整工作区冷读由原运行脚本现场检查；复核没有再次挂载或调用模型。配置、原始对话和工具输入输出未公开，清单保留其文件 SHA。

稍后的本机服务检查另见 [local-services-current.json](receipts/local-services-current.json)：Redis 和 RustFS 容器均在 23:19:52 退出，`ExitCode=255`、`OOMKilled=false`；原因尚未确定。这发生在 22:58 原生恢复结束之后，不是正常 `docker stop` 证明，也不纳入恢复通过判定。23:23/23:26 的 PID、cgroup、挂载和连接检查只说明当时已无活动残留，不能反推先前的退出原因。检查没有启动、停止或删除服务。

这次资格检查把每次模型输出限制为 512 token，关闭 thinking；最多允许 4 个不同请求，实际用了 2 个。沙箱限制为 1 CPU、768 MiB、禁止 swap，单次原生过程最长 205 秒。复制出来的工作区归属 UID 65534，原始 checkpoint 未修改。OpenCode 自报两步 token 总量分别为 9,680 和 9,718，其中缓存读取分别为 9,344 和 9,472；这不是账单金额。参数和执行源码 SHA 见回执。

7 项离线测试通过，覆盖请求预算、重复请求、工具定义改变、不完整响应流、工具 ID 对齐、源码固定及链接拒绝，见 [测试记录](tests.json)。

这是本机功能资格，不增加云上 C4 正式完成数。下一步仍需把已验证的调用限制接入云端执行入口，取得两端资源交接与容量检查，在不同 ECS 上挂载同一后端和原工作区，以空 HOME 原生恢复；分别记录状态就绪时间和首个完成工具的时间，再完成冷读、进程关闭、指标校准及归档。现有 `run-c4-state-reopen.py` 仅允许零模型调用，不能直接用作这个正式恢复测试。
