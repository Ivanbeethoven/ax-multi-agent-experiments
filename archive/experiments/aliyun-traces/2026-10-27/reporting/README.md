# 简短报告与耗时图生成器

三方法完整诊断图与报告已生成至 [summary](../summary/summary.zh.md)，采用既定 Python 后端；C1、中断和未完成结果均未入图。自动 QA 和代理视觉检查已完成，root 保留最终看图确认；候选仍未通过本地性能验收。

实际输入为 `report-inputs.json` 和 `acceptance-evidence.json`。三方法完整回放及释放核验已齐，C4 的 17 项真实 CI 门禁、git diff 检查、源码归档身份、测试计数及原始日志 SHA256 均已整理；C1 和 12 项历史记录只解释排除。报告证据链接留在 AX 仓库内。CI 通过，两轮 compose 的改善与回退完整保留，root 已明确放行开发诊断图；该放行不代表性能验收，也不批准新云资源。

先复制两份 `*.template.json` 为工作输入，核对最终 C4 JSON 的真实结构，再调整 `fields` 中的 RFC 6901 JSON Pointer。模板中 native 的部分映射仅来自已有 C1 结构，packed 未预设字段；`mapping_verified=false` 会阻止生成。不要把该标志改为 true 来掩盖缺失字段。JuiceFS 映射已经对现有 `result.json` 核实。

```powershell
python reporting/generate_report.py --manifest reporting/report-inputs.json --check
```

返回码 2、`status=pending` 表示数据／CI／释放证据未齐，且不会创建报告、图或 CSV。返回码 0、`status=ready` 仅说明数据门槛通过。所有必需字段必须来自文件；不支持用常量代替结果字段，不自动回退旧版本。

最终输入齐备且获 root 通知后，使用装有 `matplotlib`、`PyMuPDF` 和中文字体的 Python：

```powershell
python reporting/generate_report.py --manifest reporting/report-inputs.json --render --out summary --skill-scripts "$env:USERPROFILE/.codex/skills/nature-figure/scripts"
```

输出 `summary.zh.md`、`replay-seconds.png/svg/pdf`、`source-data.csv`、`provenance.json` 和 `qa/`。默认 183 × 88 mm、600 dpi；SVG 保留文字，PDF 使用 TrueType。生成器执行源码预检、单面板 alignment 记录、PDF 字号和碰撞检查；任何自动失败阻止写出报告。即使脚本成功，root 仍须在最终尺寸检查图、SVG 编辑性与每项 QA WARN；`provenance.json` 明示需视觉复核，不能声称视觉 QA 已自动完成。

准备阶段源码预检结果：20 PASS、0 FAIL、1 WARN。唯一 WARN 是未输出 TIFF；本任务明确要求 PNG、SVG、PDF，并非期刊栅格投稿包，因此保留 PNG 而不额外生成 TIFF。该说明不替代最终渲染后的字号／碰撞／视觉 QA。

数据映射：

| 输入项 | 必须映射到的实测信息 | 用途 |
|---|---|---|
| `method_id`、`run_id` | 结果文件自己的方法与运行标识 | 阻止错把另一方法／另一运行的数据或释放证据代入 |
| `completed*`、`operations`、`read_bytes`、`write_bytes`、`remaining_handles` | 完整原始回放报告 | 必须完整、1,747,109 操作、281,774,624 / 194,706,661 B、句柄 0 |
| `execution_seconds` | 回放器纯执行耗时 | 图中唯一量值；保留源 JSON Pointer 和哈希 |
| `exit_code`、`timed_out`、`fatal_error`、`cleanup_errors` | 回放／清理记录 | 必须 0、false、null、[]；缺字段不推断成功 |
| `expected_errors`、`probe_errno_differences`、`valid` | 原始报告 | 原值展示，不把 performance 完整误称严格正确 |
| `candidate`、`source_archive_sha256`、`trace_sha256` | 候选／工作负载身份 | 两个 BrewFS 必须为同一 C4；三个方法为同一 trace |
| `instance_type`、`configuration`、`configuration_note` | 原始配置与人工核实的简短说明 | 明示不同实例及缓存设置 |
| `release.fields` | 释放后核验 | 实例 0、创建磁盘 0、自有安全组 0、临时桶 false、核验时间非空 |
| `observer_*`、`uploader_cpu_seconds` | 观测器／上传器实际资源计数 | 不等于观测对延迟的因果开销；缺失写未记录 |
| `cost_estimate_cny`、`cost_scope` | 原有估算值及排除项 | 不冒充账单，不补算未知费用 |
| `preparation_stages`、`preparation_note` | 回放前固定版本准备 | pack／snapshot 构建、上传等单独报告，不进入耗时图 |

每个 `preparation_stages` 条目形如 `{"label":"实际阶段名", "seconds_pointer":"/实际/计时字段"}`，支持任意真实阶段或总计，缺失指针值显示“未记录”。`preparation_note` 应说明计时边界、是否含上传／fixture 构建以及无法拆分之处。计时都以秒为单位；不做未经说明的单位推断。

`release.result` 可引用独立释放核验 JSON，省略时读取同一结果文件；其路径相对于映射 JSON。独立核验还必须映射 `release.fields.run_id`，并与方法结果里的 `run_id` 一致。`acceptance_evidence` 的顶层 `complete`、`passed` 表示同一 C4 源码的本地 CI，仍必须为 true；完整云端 trace、字节数、句柄、退出和资源释放门槛不变。

`compose.complete` 必须为 true，且保留同归档的原始结果证据；`compose.passed` 如实记录命令／检查状态，`performance_accepted` 与 `performance_summary` 单独记录性能验收及回退。`performance_accepted=false` 可生成开发诊断图，图注与报告开头会显著标明当前本地性能问题与候选尚未通过性能验收；`performance_notice` 保存准确状态。现为纯写顺序敏感、直接随机写额外墙钟开销待归因。命令成功不等于没有回退，不删掉任何坏结果。`owner_acceptance.render_authorized` 仍须由 root 明确放行后更新。

CI 记录保留实际执行 argv、退出码和原日志哈希，`category` 对应模板的 9 类最低检查覆盖；7 项 shell 命令逐条保存，额外 packed 与严格 Clippy 检查也保留。workspace test 按参数语义识别：`cargo` 的 test 子命令必须带 `--workspace --lib --bins`，允许真实 `--offline` 和 `--config profile.test.package.brewfs.codegen-units=256`，拒绝 package/exclude/filter/no-run。不会为匹配连续子串而编造另一条执行命令，也不能使用 C3 结果代填。

`history` 仅列历史／排除原因和证据链接，不把其耗时画成完整最终结果。若补充最终 C4 中断写探针，可新增历史／诊断项，并如实说明其与旧长生命周期挂起的覆盖差异。
