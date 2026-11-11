# A 组四棵旧 C1 观测原文：核验与回收

2026-11-12 00:18:43（北京时间），同一请求绑定原 dry-run 后执行成功。[实际回执](coordinator/apply-01/result.json)的 SHA256 为 `327f176e6aa9ef315b0b00b4f7179b6fab69203e19720b2c3d5ff3771edfbb9c`。执行前后盘 0 实测可用量分别为 **20,347,506,688** 和 **29,249,671,168** 字节，差值 **8,902,164,480** 字节，等于四棵旧目录的分配量。全部五个原状态库租约为空；不据此判定 On1 容量已通过。

2026-11-12 00:08:58（北京时间）dry-run 成功，当次未删除文件。协调者命令实际退出 0，原 canonical 两池资源 guard 随进程退出释放。B 组三台保持停机，本次未连接 B worker。17:38 已回收的 staging 不计入本结果。

唯一允许范围为 `/srv/ax-objects-0/o/` 下四个明确目录，均属于原 `7084af367e0bcfad-r1-native` C1：

| 目录名 | 核验文件数 | 实际分配字节 | 根目录 mtime_ns |
|---|---:|---:|---:|
| `c1-7084af367e0bcfad-r1-native` | 16,029 | 2,301,771,776 | 1794367487715686032 |
| `cal-n-rw-on1` | 15,894 | 2,183,049,216 | 1794358382721763583 |
| `cal-n-rw-on2` | 15,894 | 2,184,876,032 | 1794360207702845618 |
| `cal-n-rw-on3` | 15,939 | 2,232,467,456 | 1794365653903184677 |
| 合计 | 63,756 | **8,902,164,480** | |

归档 manifest 中上述文件的逻辑总大小为 8,677,556,891 字节；回收候选量按实际 `st_blocks × 512` 加目录分配计算。每个文件的 SHA256、字节数、常规文件类型及单链接均与原 role archive manifest 一致；允许的目录集合只从这些成员的父目录导出。根目录设备、inode、mode、nlink、size、blocks、mtime 和 ctime，以及完整目录树指纹，见 [实际 dry-run 回执](coordinator/dry-run-01/result.json)。原 observer 身份已退出；仅允许已无监听者的原 socket。没有扩大到 C4、base、trace、状态库、Redis 或 RustFS 对象。

盘 0 设备号为 64784，原 boot ID 为 `5a984d75-dca0-4921-92fa-b021d1f799e5`。dry-run 前后可用量均为 **20,285,997,056 字节**，所以尚无实际回收量。盘总量 60,070,604,800 字节，本请求最低保留 20%，即 12,014,120,960 字节；这不是下一轮峰值容量准入。即使后续回收全部候选，也必须另算 On1 原始采样、child 和归档峰值。

## 精确来源与执行

- [请求](coordinator/request.reviewed.json)：协调者 `/srv/ax-pilot-control/preparation/a-old-c1-observer-reclaim-01/request.reviewed.json`，SHA256 `f91e7fb51a356cf266addf48a3786033323fa03cf130d249594562789b9734d5`。
- [执行工具](coordinator/reclaim-closed-object-observer.py)：SHA256 `022b0a101de5f9783e154ac5e6795595ed12fc10aa8bdfdcddcc5651f655b76a`。
- [dry-run 回执](coordinator/dry-run-01/result.json)：SHA256 `6ab6239e9b054ca0f07afa4441c0012aa71d862eddbabb49543c4b21a1a80017`。
- [fresh 边界](coordinator/boundary-01/quiescence.json)：2026-11-11T16:07:45.631436Z，原 A 父 invocation 保持 EEXIST 后 exit 1，原 Off1 闭合/零债务回执与新三角色只读 probe 共同证明无活动 worker、挂载和租约。保留原 assets、旧 relay 两项驻留服务例外。
- [正式包公开回执](coordinator/public-formal.json)与[校准外层包公开回执](coordinator/public-calibrations.json)：从本仓库已公开旧 evidence 按原始字节复制，记录 OSS 完整内网回读及匿名 HEAD。正式对象角色直接公开；三棵校准目录各绑定外层包中精确的 `<tag>/objects.tar.gz` 成员、角色 SHA 及完整归档 check。

现成工具原先从 `parallel-control-01` 的 canonical plan 查找不存在的 C4/fork-1 cell。本次只增加真实 `fork-control-01/suite/plan.json` 的独立 `lock_plan` pin，并机械要求原 fork cell、canonical A 全部 cell/cluster、历史 C1 来源的物理资源集完全相同。原两池 guard、原计划、删除允许集合和各项校验保持；6 项离线测试通过，包含错误计划路径、额外/重复/不等资源及非旧 C1 来源反例。

`prepare.py` 仅做 fresh 边界和请求准备，实际 dry-run 使用原工具的 `coordinator` 路径。完整执行参数、下载文件 SHA 与公开回执复制来源见 [执行记录](execution.json) 和 [来源清单](source-files.json)。

apply 在原窗口内执行，绑定同一个 request/tool 和 dry-run SHA。工具重新核验原边界及全部成员后，仅回收上述四个目录。原窗口已用完，不可重复执行。
