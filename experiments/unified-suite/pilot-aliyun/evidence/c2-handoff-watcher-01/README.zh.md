# C4 → C2：B 组一次性交接

协调机的 `ax-pilot-b-c2-after-c4-watch-01` 每 15 秒检查一次实际交接回执，最多等 6 小时。它在独立网络命名空间里等候，期间不向测试节点发请求，也不领取锁。

只有同一个 C4 单元正式通过、原 SQLite 已结项且 B 无租约、三个节点关闭证明通过、采集器与 C2 原草稿相符，才将实际回执 SHA256 交给 `ax-pilot-b-c2-jfs-runtime15-01`。C2 是单独的 systemd 单元，持原物理锁，只启动一次，使用已固定的二进制和已复制的输入。等待、失败或启动回执均不能算 C2 结果。

状态目录：`/srv/ax-pilot-control/preparation/c2-handoff-watcher-01/run/`。启动前的回执在本目录留档。缺少 `launch-intent.json` 时可用 `systemctl stop ax-pilot-b-c2-after-c4-watch-01` 停止等待，随后复查 C2 单元仍未启动。如果已有启动意图，先检查 C2 原单元、日志和输出目录；不要自动停止或重跑 C2。
