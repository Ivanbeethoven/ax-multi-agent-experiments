# Windows 保存，WSL 继续：小任务实测

2026-10-23。OpenCode 1.19.4，现有 DeepSeek V4 Flash 配置。源端是 Windows 原生程序；目标是 Ubuntu 24.04 WSL2 原生 Linux 程序，数据复制到 ext4，目标没有直接使用 `/mnt/c` 下的源工作区。

**结果：原生 export/import 加上新路径提示，可以继续这个小任务。直接复制数据库不行。**

| 路线 | 实际结果 |
|---|---|
| 只复制完整工作区，原数据库直接 resume | 失败。读取 project 时拒绝 Windows 绝对路径；没有进入模型步骤 |
| 原生导出、目标用新数据库导入，再 resume | 会话和口令恢复了，但模型沿用旧 Windows 路径，写错文件位置；判失败 |
| 相同导入方式，额外告知当前 Linux 项目路径 | 通过。原 session ID，正确写出 final.txt，checkpoint.txt 不变 |
| 只带项目文件和无密钥配置，不带会话状态 | 报 Session not found，没有记录到模型步骤，也没有生成 final.txt |

另有一次环境配置失败：测试清空环境变量时漏掉了 WSL 必需的网络代理，启动等待约 127 秒后主动终止。保留在 `result-import-missing-proxy.json`。这不是恢复性能数据。脚本现保留网络代理设置，同时清空用户状态目录。

## 小任务怎样判断成功

1. Windows 首轮只创建内容为 `ready` 的 checkpoint.txt。提示中给一个随机口令，但禁止写入项目文件；脚本逐文件确认项目中没有它。
2. 源进程正常退出后导出原会话，把导出文件放在工作区。复制整个工作区，目标启动前逐文件比对 SHA256。
3. WSL 从空 HOME 开始，凭据经控制程序 stdin/环境变量单独注入。工作区扫描确认不包含实际 API 密钥。目标用新数据库执行原生 import，保留原会话 ID，不修改导出的对话内容。
4. 续跑提示只要求把旧会话中的口令写到 final.txt，不重述口令。最终通过的路线另告知当前操作系统和路径，不重述任务决定。
5. 校验 final.txt 的确切内容、原 checkpoint.txt、session ID 和不带状态的反例。不能只凭 Agent 回复“完成”判成功。

通过组：43 个文件，6,200,101 字节，复制前后摘要一致。目标 HOME 在 import 之前为空。strace 记录目标续跑的文件系统调用，检查中没有发现访问 `/mnt/c/`、原用户 `.config` 或 `.local/share` 的记录。该检查是访问记录审查，不是强制隔离；WSL 与 Windows 仍属于同一台物理机器。

通过组原生导出约 0.796 秒、导入约 0.548 秒、续跑约 4.926 秒。续跑含模型等待和 strace 开销；总计时还含反例，不能拿这些数字当迁移性能结果。

## 模型用量

只创建了一份成功源会话，后续反复使用该保存点。源任务、写错路径的续跑、最终通过的续跑，各记录到两次 step_finish，共六次可见模型响应。总计 47,911 tokens，其中缓存读取 26,752、非缓存输入 20,507、输出 393、推理 259。配置错误、直接复制失败和负对照没有记录到模型步骤。

这些是 CLI 事件中的用量，不是账单，也不能保证包含框架的辅助请求。自定义模型显示的 cost=0 没有可靠定价依据，因此实际费用记为未知，不能写成免费。

## 复现

从仓库根目录执行。需要 Windows Python、Git、npm，及 WSL 中的 Python 3.12、Git、strace 和 npm。

```powershell
npm install --prefix runs/opencode-resume/runtime-windows --no-audit --no-fund opencode-ai@1.19.4
wsl -d Ubuntu-24.04 -- bash -lc 'npm install --prefix ~/ax-opencode-resume-runtime --no-audit --no-fund opencode-ai@1.19.4'
python scripts/opencode_resume_probe.py `
  --source-config "$env:USERPROFILE/.config/opencode/opencode.json" `
  --windows-cli runs/opencode-resume/runtime-windows/node_modules/opencode-windows-x64/bin/opencode.exe `
  --linux-cli /home/hxy/ax-opencode-resume-runtime/node_modules/opencode-linux-x64/bin/opencode `
  --transfer-mode import --announce-relocation
```

把 Linux 用户路径改成自己的路径。外部配置须有 `provider.deepseek`、`deepseek-v4-flash` 和可用 API key；可用 `--provider`、`--model` 选其他已有配置。该命令会实际调用模型。单个 Agent 运行最多四步、等待上限 240 秒；不是按 token/账单强制限额。再次迁移已有保存点可增加 `--resume-source-run runs/opencode-resume/<运行编号>`，避免重新生成源任务。

`--transfer-mode direct` 测直接复制，省略 `--announce-relocation` 测没有路径提示的原生导入。失败组也必须保留。原始数据库、归档和凭据相关运行材料只留在已忽略的 runs/；本目录保留脱离凭据的事件、结果、文件访问记录、用量与摘要。

## 对后续实验的影响

- AX 包装入口需要记录原会话 ID，并告诉 Agent 当前运行环境和项目路径。Linux 云端两端统一 `/workspace/repo`，可以避免这次跨系统路径变化，但仍须实测。
- Windows → Linux 暂用原生导出/导入，导出文件随工作区移动，导出导入耗时计入迁移停顿。不把这条路线写成数据库直接可搬迁。
- 这次只证明一个完整对话轮次结束后的文本任务能继续。没有验证在途工具、后台进程、子 Agent、附件、undo、预算恢复、旧写者隔离或 BrewFS 上的 SQLite 行为。
- 固定版本源码说明了直接复制失败的原因：[数据库路径校验](https://github.com/anomalyco/opencode/blob/v1.19.4/packages/core/src/database/path.ts)。原生 import 会重设会话的项目和目录：[导入实现](https://github.com/anomalyco/opencode/blob/v1.19.4/packages/opencode/src/cli/cmd/import.ts)。历史工具结果中的旧路径仍保留，这正是第二组失败的来源。
