# 实验 Agent 分工与交接

一个owner负责一组实验，在步骤之间检查配置、证据和结果。被测Agent数量单独计数。主会话维护统一版本、资源分配和跨组冲突。

| owner | 负责内容 | prompt |
|---|---|---|
| c1-owner | 共享后端trace主对照与BrewFS内部对照 | [C1](agents/C1.prompt.md) · [首次试跑](agents/C1-pilot.prompt.md) |
| c2-owner | Pack与Native＋TiKV的只读ML小文件 | [C2](agents/C2.prompt.md) |
| c3-owner | RustFS布局、全局HTTP并发和分层读字节 | [C3](agents/C3.prompt.md) |
| c4-owner | fork/clone/cp，以及两文件系统的stay/move | [C4](agents/C4.prompt.md) |

四组可并行准备；同时测量时各组使用独立可变服务池。只读输入和相同构建产物可共享。

## 交接字段

```json
{
  "campaign_id": "填写本批唯一ID",
  "cluster_id": "C1",
  "phase": "prepare",
  "provider": "ssh",
  "profile": "pilot-8",
  "study": "baseline",
  "repo_sha": null,
  "plan": null,
  "fleet": null,
  "run_dir": null,
  "authorized_cells": [],
  "input_and_artifact_pins": null,
  "retention_location": null
}
```

所有路径使用执行方环境的实际路径。phase=prepare交付配置与检查；run需填写明确条件和已有授权。现阶段只准备。

## 共用执行规则

1. 阅读AGENTS、对应方法和[SSH操作手册](SSH-RUN.zh.md)，冻结代码、构建、输入与配对顺序。
2. 填实际节点、槽位、共享宿主和服务关系；完成资源、内容及[观测资格](OBSERVABILITY.zh.md)。
3. 每次执行一个步骤，检查退出码、原始输出及SHA；全部worker就绪后由cohort一次放行。
4. 保存所有结果和异常。连接中断先核原进程；重试使用新attempt并保留原记录。
5. 应用完成后核drain、独立读回及完整采样，下载并核验保全清单。
6. 收尾按本批所有权处理；现有容器/主机及原数据由提供方管理。

C4额外的fork重试、写者释放和删除步骤集中在[Manager](MANAGER.zh.md)与[迁移](MIGRATION.zh.md)，各prompt只补本组差异。
