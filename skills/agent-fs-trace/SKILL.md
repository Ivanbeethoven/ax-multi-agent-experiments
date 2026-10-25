---
name: agent-fs-trace
description: 采集真实AI Agent整个任务期间的仓库与HOME/XDG文件访问，保存FUSE回调及真实写入内容，用Rust回放并核验状态。用于OpenCode文件系统trace采集、已有trace回放和负载归档。
---

# Agent文件系统trace采集与回放

把代码仓库和Agent状态目录放进同一个FUSE passthrough挂载；让真实Agent及其子进程通过挂载读写；停止整个任务后，使用冻结的初始文件和写入内容逐条回放。默认样例是OpenCode 1.19.4＋deepseek-v4-flash＋SWE-bench Verified。

## 先选择执行方式

- 新采集：读[仓库与版本](references/repositories.md)、[完整执行命令](references/commands.md)第1—5节；先做无模型预检，再执行用户指定的真实任务。
- 回放现有数据：读[完整执行命令](references/commands.md)第6节。不需要模型密钥或模型调用；普通已挂载目录回放也不需要FUSE设备。
- 数据验收、归档：读[格式与验收](references/format-and-checks.md)及命令第7节。
- 改Agent或运行自己的仓库：沿用第8节的挂载和状态目录规则，不将四题专用入口称作任意任务入口。

## 工具位置

本skill的`assets/runtime/`带有采集器、完整任务入口、Rust源码/Cargo.lock、统计与回放脚本；可从该目录直接构建和执行。版本和原文件SHA256见`assets/runtime/source-manifest.json`。入口的本机路径已换为环境变量，原实验仓库未被改写。

直接从本仓库使用时，在仓库根目录设置`export SKILL="$PWD/skills/agent-fs-trace"`，再执行references/commands.md中的命令；无需先安装skill。

完整FUSE格式使用`tools/full-fuse-replay`。另一个独立仓库`fuse-workload-replay`是早期v1工具，不能接本批events.jsonl。

## 执行规则

1. 新采集需要Linux、可用Docker daemon、/dev/fuse、实际挂载许可及user_allow_other。只有已分配普通容器且不能挂FUSE时，使用已有数据回放。
2. 将repo、HOME和全部XDG状态目录放入记录挂载。模型密钥留在宿主转发器，通过stdin读入；挂载里仅写本地转发地址和占位值。不得把宿主真实HOME映射进去。
3. initial和initial-tree必须在模型启动前冻结。trace、blob、Agent日志放在被记录目录外，避免采集器记录自己。
4. 每次新输出目录。预检通过、两条容器路径确认属于FUSE后再调用模型。不设模型步数上限，保留原入口3600秒时间上限；不要悄悄增加尝试次数。
5. 记录器退出时停止任务。停止整个Agent容器、关闭句柄/映射后正常卸载；采集异常或缺destroy不能标成完整。
6. 回放使用新初始副本、原写入字节和同一顺序。检查读内容、返回长度、errno、计数、最终树、硬链接关系和会话数据库。任务修复是否成功另记；filesystem-only模式的resolved为null。
7. 记录代码/镜像/输入摘要、退出状态和失败原因。归档前扫描已知密钥；扫描失败就保留本地原始数据，不能修改数据后继续声称原样回放。

## 结论范围

记录的是挂载点收到的高层FUSE回调；包括落到挂载点的子进程和mmap读写。内核缓存命中、本机锁处理、镜像运行库、网络和/tmp不在此范围。采集与回放均串行，属性/目录缓存超时为零，会改变原运行时间和请求数。

回放时间包含JSON解析、SHA256和路径检查。实际读写字节不是物理磁盘流量；重复读取比例不是缓存命中率。回放成功表示文件状态可重演，Agent移机resume需要独立测试。

## 交付

报告输出目录、输入/工具版本、回调数、实际读写字节、重复读取比例、预检/回放/最终树/数据库检查结果，以及Agent退出状态。新模型运行、上传和发布依照用户本次任务范围执行；本skill不会自动申请云资源或发布数据。
