# 执行适配与观测准备验收（2026-11-01）

本记录只证明软件和本地夹具验证，不是C1–C4性能成绩，也不证明云端或真实产品服务已经就绪。未采购、未部署云资源，未启动正式实验。

## 本轮范围

- C1按最新要求缩为8 Agent、两个执行节点各4个；独立RustFS、Redis、协调节点各一，共5台，规划168vCPU/672GiB。
- `pilot-8`为4 cohort；`c1-8`为主线84＋内部对照36。两provider命令均实际生成计划和准确inventory模板；资源ID仍待填写，不授权采购。
- C2–C4保留既定矩阵。挂载监督、单cohort、S3请求分片、clean stay/move、原始观测共用 `bench/runtime/`，无第二套云专用runner。
- 不运行旧64/128 C1，不重投历史已完成项，不删除历史慢值/失败。

## 实际完成的验证

| 检查 | 实际结果 | 范围 |
|---|---|---|
| Linux Python全套测试 | 151项：140通过、11明确skip、0失败 | 其中runtime 21项；skip需要真实trace/指定Rust二进制，未计为通过 |
| Rust ml-corpus-scan | 2个单元测试通过；fmt检查通过 | 摘要/长度/路径错误边界 |
| 实际Rust CLI夹具 | 2048文件，stat/read/verify各完成；read/verify均8,388,608 B，访问延迟桶合计各2048 | 本地临时语料，非ML性能结果 |
| 损坏文件夹具 | 原生CLI非零退出，errors=1，访问桶仍计2048次 | 保留失败访问，不从分布删除 |
| 分布式控制协议 | 64个真实本地worker全部ready后放行；576个夹具操作记录完整 | 本地传输；不证明云网络或真实FUSE并发 |
| 观测采集 | 实际/proc、本地HTTP、后台collector及Unix控制socket、阶段mark、stop和audit通过 | 原生BrewFS/JuiceFS/RustFS endpoint仍待实机资格 |
| 失败门 | 改输入不放行；结果不匹配保留实际JSON；源lease未释放不启动目标；缺指标/计数回退/断档/摘要变化拒绝 | mountinfo/API部分使用受控替身 |
| 静态检查 | Python语法、43份入口文档链接、构建shell语法、所改文件diff空白检查通过 | 不编译或宣称产品二进制已验收 |

完整Python测试日志留在本地 `runs/runtime-adapter-20261101/tests.log`，SHA256：
`ac91c8869f02ded9ac13f0db7be88b966ae2353fe0f18e0c5109f987a9e4f4c9`。

本地ML夹具debug二进制SHA256：`68b0e7e7ea8391a56c41ae72c2633bc771f6fc7d16aa29abffd10b68960e14ba`；不是正式部署产物。

## 测量完整性

输入摘要检查在统一屏障前，不把大trace重新哈希计入任务时间。每任务保留实际原生JSON、SHA、stdout/stderr、退出码、进程身份和失败原因；经验分位数按workload分别计算并注明样本数。S3失败仍留请求index、实际短响应字节、状态、耗时和未启动数量。

原始观测覆盖全部角色，绑定指标名称、源码/产物、层次、单位和分母。指标采样、阶段记录、计数器连续性、文件摘要、至少三对on/off开销均是独立门。cohort的passed只代表工作负载与live检查，performance_accepted始终待drain、独立读回、观测audit和保全验收。

BrewFS源码审阅版本为 `918deb90e046889d94eb8ff633822e275792ce28`，构建脚本已显式加入observability。分发前仍须实际编译、help/endpoint核查及SHA验收；不能复用缺该feature的旧产物。RustFS仍按指定分支fresh pin并核镜像来源。

## 正式开跑前仍需取得

1. 地域、真实资源清单、费用上限与保留时长；实际host-key、CPU/内存/盘/网络和发压能力验收。
2. 本批产品构建、非空native lower、真实FUSE、等工作量与内容/errno资格；C2另需三节点TiKV及真实大语料，C4另需原生两节点会话与fork/clone资格。
3. 每个实际endpoint的完整指标合同、目标密度采集开销、原始采样覆盖和保全空间资格；缺内部计数不得冒称读放大。

执行入口见[适配手册](../runbook/ADAPTERS.zh.md)、[观测验收](../runbook/OBSERVABILITY.zh.md)和[8 Agent C1方案](../C1-ALIYUN.zh.md)。
