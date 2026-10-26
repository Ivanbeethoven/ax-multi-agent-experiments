# 配置名称与证据边界

2026-10-26，只读核对。作者三个名称表达三组实验配置，不宣称是逐字 CLI 模式。

- 既有源码审计对应 `242af5f7e9bba7afc0c61778f1084fcb20e083a0`，见论文仓库 `evidence/principles-code/`；本轮又读取本地 `brewfs-upstream` 的 `f80e7148c82e9f1ef235ab19318b89cb41a093a2` 核对入口。两者不代表执行当天最新版。
- `src/workspace_overlay/meta_layer/packed_lower.rs:230` 将原生 catalog lower 与 Packed lower 并列；`WorkspaceLower::Native` 不等于 `native_base` 模块。
- `src/cli.rs:1949` 后创建 WorkspaceMetaLayer，再读取可选 lower binding；有绑定才 attach Packed。这支持“Overlay 不要求 Pack”，不能单凭该段证明带修改父状态 fork 或跨节点迁移已贯通。
- `src/cli.rs:912` 的 `native-tikv` 是固定 Packed manifest 的只读元数据放置对照，不直接等于作者的 `native` 基础配置。
- 执行时记录三组真实命令、lower 类型、上层及数据路径。源码发现、作者意图和运行验证分开；此处没有性能结果。

执行资料依据：会话“规划多 Agent 共享数据实验”、实验仓库提交 `734eb9b2b5eb147e003514f57036a6c558a05407`、作者对可选机制、数据库减压和首次 fork 的说明。归档身份直接复制现有 archives.json，未下载数据或运行云实验。
