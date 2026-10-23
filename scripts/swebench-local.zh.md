# 在本机跑四道较难 SWE-bench Verified 题

运行入口是 `run_swebench_opencode.py`，它在 WSL 中控制 Docker。发布结果用 `publish_swebench_results.py`。这与原先固定的 16 题计划是两组独立试验。

## 固定条件

- OpenCode 1.19.4，模型 `deepseek-v4-flash`。用独立 provider 名 `experiment`，避免 OpenCode 内置模型别名改写请求 ID。
- 数据集和评分代码的 SHA 固定在脚本中。选择四个仓库中难度标签最高的题，再按 ID 字符串顺序取首题；不读参考修复来决定题目。
- 首轮每题最多 40 步、20 分钟。用户随后要求取消步数限制并重跑未通过的题；新一轮从原始代码开始，不设步数限制，单题超时 1 小时。两轮分目录保存，通过的题不重跑。同时最多两题，每容器 4 CPU、6 GiB 内存。时间上限不是按美元计费的硬上限。
- 原始代码失败、参考补丁通过后才调用 Agent。Agent 不挂载数据集、参考补丁、评分脚本、宿主 HOME 或 Docker socket。只挂载 OpenCode 程序和该题私有状态目录。
- 模型凭据从控制程序 stdin 读取，由 `docker exec -e AX_SWE_API_KEY` 注入；配置文件仅保存变量引用。不要把包含密钥的配置提交到仓库。
- 本机代理监听 WSL 的 localhost，Agent 容器使用 host 网络；评分容器沿用默认网络。网页搜索工具关闭，提示禁止查答案，但 shell 网络不是强制隔离的，应审查工具记录。

## 准备

WSL 需要 Docker、skopeo、Git、Python 和已安装的 SWE-bench 评分环境。本次使用 `/home/hxy/research/agent-fuse-swebench/.venv/bin/python`，评分仓库为其旁边的 `SWE-bench`，commit 为 `02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`。

先运行脚本的 `--prepare-only`，生成选题清单。按照 `selection.json` 中的四个 image 拉取镜像。本机守护进程代理有故障，因此实际用下面的方式逐个导入；它不改变镜像内容，也不重启 Docker：

```bash
skopeo copy --retry-times 2 docker://docker.io/<selection.json 中的镜像名> docker-daemon:<同一镜像名>
```

## 执行

**现在默认禁止直接启动新的模型解题。**此前的脚本只有 Agent 事件记录，不能保证文件系统 trace。已接通的单进程文件采集先用 `run_fs_trace_pilot.py`，见 [采集与回放说明](fs-trace.zh.md)。完整 OpenCode（多进程、SQLite、mmap 等）的采集尚未通过验收。仅在明确接受“不采集文件系统 trace”并复现旧运行时，才能添加 `--allow-agent-events-only`；该参数不会开启文件系统记录。

在 Windows 仓库目录用 PowerShell 读取已有配置，再经 stdin 交给 WSL。下例的路径按本机实际情况修改；第一次执行输出目录内不应存在任务子目录。

```powershell
$taskConfig = Get-Content "$env:USERPROFILE/.config/opencode/opencode.json" -Raw | ConvertFrom-Json
$taskConfig.provider.deepseek | ConvertTo-Json -Depth 12 -Compress |
  wsl -d Ubuntu-24.04 -- /home/hxy/research/agent-fuse-swebench/.venv/bin/python `
    /mnt/c/<实验仓库路径>/scripts/run_swebench_opencode.py `
    --output /home/hxy/ax-swe4/<新批次目录>
```

可用 `--instance-ids` 只执行预先固定的部分题目；不要突破两题并发限制。默认不设步数限制，`--timeout-s` 默认 3600。重现首轮时显式传 `--max-steps 40 --timeout-s 1200`。重跑使用全新的输出目录，保留首轮失败；报告两轮结果及总用量，不只呈现最好一次。

脚本将 Agent 补丁应用到全新评分容器，用数据集原始 eval_script 执行，由固定版本 `get_eval_report` 解析。未解决、超时、空补丁、补丁不能应用、评分环境错误均保留，不从四题分母删除。参考补丁评分只用于环境检查。

## 发布

四题都有 result.json 后，在 Windows 上运行发布脚本，`--source-root` 指向 WSL 原始结果目录，例如 `\\wsl.localhost\Ubuntu-24.04\home\hxy\ax-swe4\<批次>`，`--credential-config` 指向原 OpenCode 配置。

发布器只复制指定的输入、日志、补丁、评分报告和环境记录；不复制数据库、缓存或完整配置。它扫描已知 API 密钥并记录脱敏次数，最后重新扫描并生成文件摘要。人工检查 README 与四份评分报告后，再提交 GitHub。

真实会话数据库仍保存在本机各题的 `private-agent-state`。`export_swebench_trace.py` 可在禁网容器中导出已完成会话，不调用模型。`trace/` 保存原生会话 JSON 和存在的长工具输出附件；清单包含 SHA256。文件 read 工具按范围读取的内容就是实际返回内容，不视为丢失的日志。本次没有插入迁移，没有保存原始模型 HTTP 请求，也没有采集 POSIX/FUSE 读写轨迹。Agent 事件日志不能直接当作 Rust 回放器的输入。
