# C4 跨机：在源端准备原生会话

`scripts/prepare-c4-native-handoff-source.py` 只在已挂载的源端安装一次原 checkpoint，核对闭合的 SQLite 数据库和完整目录，再调用原 bounded worker 导出；原生 export 的 SHA-256 必须与冻结输入一致。不会调用模型、导入会话、启动目标端、停挂载或标记实验通过。

由同一位操作者持两池物理锁。先按原路准备并挂载 A 池的专用共享卷，确认正确的 Redis DB、RustFS bucket、源客户端身份和运行 slot。把已有 checkpoint 包校验并解出到卷外；保留原 `receipt.json`。源端安装使用新尝试目录和空的 `/srv/ax-pilot-session/workspace`，不能覆盖旧内容。

独立 relay 使用两个客户端都能访问的相同 loopback URL（例如通过受审阅的私网 SSH 转发），不要占用旧 18443 端口。客户端配置从冻结的 `workspace/config/opencode.json` 派生，只改 `provider.experiment.options` 中的 `baseURL` 和独立 relay key，以及 `provider.experiment.models.deepseek-v4-flash.limit` 为 `{"context":0,"output":512}`。配置文件由操作者保管，权限 `0600`；provider master key 只在 relay 主机。脚本校验配置差异，不复制这份私有配置到证据目录；只保留安装后配置的路径和摘要。不要把活动 workspace 或私有配置归档。

请求字段如下。每个 pin 均是 `{"path":"<绝对路径>","sha256":"<实际值>"}`，草案先保留 `reviewed:false`。审阅件另存。

| 字段 | 内容 |
| --- | --- |
| `schema` / `reviewed` | `c4-native-handoff-source-install-v1` / 操作者审阅值 |
| `source` | `{"role":"client","side":"source","pool":"pool-a","hostname":"<本轮源客户端真实主机名>"}` |
| `mount` | `spec` 为本次已审阅源 mount spec 的 pin；`directory` 为本次 mount supervisor 的绝对路径 |
| `checkpoint` / `checkpoint_receipt` / `source_tree` | 冻结的 `checkpoint.json`、原打包 `receipt.json`、解包后的 `source-tree.json` |
| `tree_reader` / `worker` / `binary` | 同版本 `prepare-session-input.py`、`session-worker-bounded.py`、原 `opencode` |
| `private_client_config` / `relay_url` | 审阅的私有客户端配置 pin；与配置一致的新 loopback URL |

由原 slot 的受审阅执行入口调用（worker 会校验实际 cgroup 和预算）：

```sh
python3 -B experiments/unified-suite/pilot-aliyun/scripts/prepare-c4-native-handoff-source.py \
  --request /private/source-install.reviewed.json --sha256 "$SOURCE_INSTALL_SHA256" \
  --out /srv/ax-client/q/c4-native-source-install-01
```

输出 `result.json` 含 export、bounded worker 与新配置的摘要，以及真实的共享路径：`workspace/config/opencode.json` 和 `workspace/source/{data,state,cache,config}`。后续两端的 handoff spec 必须用这些路径。失败后保留首次输出和现场；确认退出、挂载和工作区状态后再安排新尝试，不自动重跑。

离线测试使用真实临时 SQLite、文件复制和 CLI 子进程，模拟 FUSE supervisor 与 export；尚无云端安装或跨机验收。仍需现役源挂载、目标端与独立 relay、五份 cohort、实际恢复和收尾核验。
