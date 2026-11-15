# A 组 C4：首轮 Off1 已完整结束

2026-11-11 23:41，原 `c4-a-flat-off1` 完成 10 万文件的复制、修改、完整冷读、排空、正常卸载和归档。[原 finished 回执](finished.json)及其引用文件均逐字节取回并核 SHA，见[来源清单](source-files.json)。这是第一轮校准，正式 C4 完成数不变。

| 项目 | 原始结果 |
|---|---:|
| 协调者记录的任务时间 | 8,453.37 秒 |
| 完整冷读校验时间 | 4,916.56 秒 |
| 父目录冷读 | 100,000 文件，409,600,000 B |
| 修改后 child 冷读 | 100,000 文件，409,591,841 B |
| 上传排空阶段 | 1.662 秒 |
| 归档 | 20,647 B，57 个成员 |

父目录与原输入一致，child 与预先固定的完整预期树一致。两次挂载均正常退出，无剩余挂载和上传债务。[客户端完整校验](raw/client/q/c4-a-flat-off1/verify/result.json)、[收尾](closure.json)、[归档核验](archives/client-check.json)保留原路径与摘要。cohort 中的 `performance_accepted=false` 保留原义：它是计时结束时的中间回执，不能单独代表整个试验验收。

23:57 已公开[客户端原始包](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c4-a-flat-off1-01/client.tar.gz)，SHA256 为 `7239c02e4a4f69a3d3152e7f4002fe7eb396deef15ab46c268b2af618d36a6fa`。[上传回执](publication/client.tar.gz.json)记录完整读回与匿名 HEAD；[独立下载核验](publication/independent-client-check.json)确认 57 个成员全部一致。

[原父进程随后已收尾](../a-c4-parent-close-01/README.zh.md)，第二轮未启动。Off1 仍会作为后续 `touched-cold-v1` 的完整参考，原结果及工作区不进入本轮清理清单。
