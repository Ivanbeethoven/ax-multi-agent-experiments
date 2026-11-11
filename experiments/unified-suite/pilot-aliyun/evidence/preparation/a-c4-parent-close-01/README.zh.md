# A 组已停在第二轮之前

2026-11-11 23:47:37，独立工具核对原服务、PID 启动身份、Off1 完整冷验和收尾回执、子进程真实退出状态及空 `trial-2/`。默认检查通过且未发信号；23:47:50 再次核对后，仅通过 pidfd 向原父进程发送一次 SIGCONT。

23:48:08 [终态检查](operation/terminal-observation-01.json)确认：

- 原父进程自己回收子进程，写出 [`trial-1/exit.json = 0`](original/trial-1/exit.json)。
- 原父在第二轮目录已存在处停止，未启动 On1；[`failed.json`](original/failed.json)保留真实 EEXIST 原因。
- 原三角色收尾检查全部通过，无活动负载、挂载或 Agent 槽进程；所有已发现状态库的 leases 为空。
- 原服务 `MainPID=0`、`ExecMainStatus=1`，原父子 PID 均消失。父服务的退出 1 来自预设停点，不覆盖 Off1 的成功结果，也不表示后续五轮已完成。

[工具与操作回执](source-files.json)已保存。新增 13 项离线测试由主 Agent 独立复跑通过，覆盖过早恢复、PID 变化、失败子进程、非空停点及 pidfd 打开后的身份变化。后续继续按[逐轮入口](../../../C4-A-DIRECTORY-HOLD.zh.md)执行，先完成归档、清理和新容量检查。
