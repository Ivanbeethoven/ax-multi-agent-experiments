# Manager：C1/C4 接入与完成判据

本页只准备接入，不授权部署或开跑。已核对 [BrewFS PR #390](https://github.com/brewfs/brewfs/pull/390) 的合并提交 `1e7a95c10cbff8d34faf768ebd621ae8261cbd91`；[审查记录](../evidence/manager-pr390-review.zh.md) 与后续 [接口设计](../CONTROL-PLANE.zh.md) 分开保存。执行方必须重新冻结实际版本、产物 SHA 和 capability；后续 main 的结果不能继承本次测试结论。

## 1. 实际能力与接入选择

| 项目 | #390 已实现 | C1/C4 如何使用 |
|---|---|---|
| catalog | 服务端配置的本地 SQLite、`workspace-v1`，单 Manager、持久操作日志 | 只能做该格式的本地 API 资格；不能替换正式共享 Redis/TiKV 或 native-v2 布局 |
| 卷 | 初始化空 catalog，或核验并登记已有真实卷 | 与旧 Console `/api/volumes` 配置登记区分；初始化不产生实验语料 |
| fork | 精确不可变 revision；每请求 1–100 个子 workspace；逐项恢复 | 核全部子项完成；没有自动封存活跃源或挂载 |
| 删除 | 检查有效 lease 后写逻辑删除标记；保留共享 lower | 不等于卸载、数据排空、物理回收或云资源释放 |
| 节点操作 | 无 checkpoint、mount、drain、handoff、remote-ready 证明 | C1 就绪与 C4 两节点交接继续用已完成资格的原生路径；缺少适配则保持未就绪 |

每批保存 `manager-integration.json`：实际 source/binary SHA、原始 capabilities、格式/后端、单实例与 catalog 身份、所选路径、缺失能力。#390 对本轮共享服务池应记录 `integration_status=local_api_only`、`formal_c1_c4_compatible=false`。不能通过把 SQLite 放进网络共享目录来声称已支持分布式元数据。

## 2. 单步调用约定

以下是执行授权后的独立命令模板，不是整轮启动脚本。`MANAGER_API`、本轮资源 ID 和路径来自执行方调度单。`MANAGER_AUTH_CONFIG` 是仅执行账户可读的 curl 配置，含既有 Console 管理员鉴权；不进入 Git、报告或请求日志。管理员 token 对配置的全部 catalog 有权限，`owner` 标签不是租户隔离。

`MANAGER_STEP_DIR` 每次请求使用独立目录，保留原始响应和 HTTP headers，不覆盖前次尝试。任何变更前保存 `request-manifest.json`：方法、完整路径、请求文件及 SHA256、`Idempotency-Key`、本轮归属、预期源/目标 ID。key 为 1–128 个可见 ASCII 字符；不得含凭据。下面的 shell 变量必须先从这些已核验记录取得。

### ① 查询能力

```bash
curl --config "$MANAGER_AUTH_CONFIG" --fail-with-body --silent --show-error \
  "$MANAGER_API/api/v1/capabilities" --output "$MANAGER_STEP_DIR/capabilities.json"
```

检查 `enabled`、`formats`、`metadata_backends`、`max_fork_count` 及节点能力。capability 中 `remote_ready=false` 表示服务没有提供该证明，不表示已测得所有数据未上传。能力不匹配时记录缺口，不能继续调用设计草案中的接口。

### ② 初始化或登记卷

先按冻结版本的 [产品启动说明](https://github.com/brewfs/brewfs/blob/1e7a95c10cbff8d34faf768ebd621ae8261cbd91/doc/operations/manager.md#start-one-instance) 配置本轮 `STATE_DIR/manager-catalogs.json`、持久状态目录、单实例与鉴权；仅在取得部署授权后启动。请求不携带路径或后端凭据。初始化请求文件的形状如下，引用和 owner 要改为本轮实际值：

```json
{"mode":"initialize","catalog_ref":"local-qualification","confirm_empty":true,"owner":"c1-qualification","volume_format":"workspace-v1"}
```

已有卷采用 `mode=register`，提供读取真实 header 得到的 `expected_volume_id`，不带 owner，不设置 `confirm_empty=true`。不能先初始化再声称登记了旧卷。

```bash
curl --config "$MANAGER_AUTH_CONFIG" --fail-with-body --silent --show-error \
  --request POST "$MANAGER_API/api/v1/volumes" \
  --header 'Content-Type: application/json' --header "Idempotency-Key: $IDEMPOTENCY_KEY" \
  --data-binary "@$REQUEST_JSON" --dump-header "$MANAGER_STEP_DIR/headers.txt" \
  --output "$MANAGER_STEP_DIR/operation.json"
```

保存返回的 operation `id`、`volume_id` 和 `Location`。收到 202 只表示接收。超时或断连时不推断操作未发生：有 operation ID 先查询；没有则经监督核查后以原路径、原请求、原 key 重发并保存新尝试。不能重新生成 key 或删日志重做。

### ③ 分别确认操作与资源

```bash
curl --config "$MANAGER_AUTH_CONFIG" --fail-with-body --silent --show-error \
  "$MANAGER_API/api/v1/operations/$OPERATION_ID" --output "$MANAGER_STEP_DIR/operation-state.json"
```

```bash
curl --config "$MANAGER_AUTH_CONFIG" --fail-with-body --silent --show-error \
  "$MANAGER_API/api/v1/volumes/$VOLUME_ID" --output "$MANAGER_STEP_DIR/volume.json"
```

状态以实际 JSON 的 `accepted/running/succeeded/failed/recovery_required` 为准。前两项继续观察；失败/待恢复先查原因、实际 catalog 和已完成项，再决定是否重发原请求。只有全部目标成功才推进。`metadata_ready=true` 不是 mounted 或 remote-ready；资源中的 `remote_ready=null` 表示未知。Manager 重启保留原日志、catalog 和配置，不重建空环境来消除恢复错误。

### ④ 从冻结 revision fork

初始化返回的 `root_revision` 是空基底，不能用来测 1M 文件或 400 GiB 的 fork。先走该格式已验证的语料发布步骤，冻结非空源的 `layer_id`、`sealed_version`、32 字节 `root_hash` 和语料清单。Manager 无活跃源 checkpoint API。查询 `/api/v1/volumes/$VOLUME_ID/revisions/$LAYER_ID` 核对完整 tuple，不能只比 layer ID。

冻结请求文件 `{"revision": <完整 BaseRevision>, "count": <本批数量>, "owner": <本轮 owner>}`；尖括号仅说明类型，不是可执行 JSON。按步骤②的命令逐字段保留，仅把 URL 改为 `/api/v1/volumes/$VOLUME_ID/forks`，使用此 fork 请求自己的固定 key 和文件。

按步骤③查询 operation，核数量、每项 `succeeded`、唯一 workspace/head ID、原 revision；分别查询 `/api/v1/volumes/$VOLUME_ID/workspaces/$WORKSPACE_ID` 并保全返回值。列表为分页接口，默认一页不能证明所有子项齐全。

部分成功、响应丢失或重启后，只对同一操作重发原始整份请求与原 key，让服务按原 ID 对账；不减 count、不换源、不新建 key 重做全批。已完成子项仍属于原批。恢复期间子 workspace 已被推进或源身份不匹配时，停在冲突/待恢复状态，不回退 head 来“修复”。

单次上限 100；128 子副本须预先冻结两个明确批次（例如 64+64）及各自 key，报告共同开始至全部完成、排队、逐批结果及失败。不能把两请求称为单次原子 fork，也不能把串行处理称为并行。#390 的单实例协调器并不构成正式 128 Agent 吞吐资格。

### ⑤ 安全删除

先核本轮资源归属、完整结果及 SHA 已保全、应用停写、该格式原生 drain/远端证明、真实卸载和 lease 释放。节点失联或 lease 到期不足以证明待上传数据已保全。C4 move 不删除源 workspace 来代替写者释放。

```bash
curl --config "$MANAGER_AUTH_CONFIG" --fail-with-body --silent --show-error \
  --request DELETE "$MANAGER_API/api/v1/volumes/$VOLUME_ID/workspaces/$WORKSPACE_ID" \
  --header "Idempotency-Key: $IDEMPOTENCY_KEY" \
  --dump-header "$MANAGER_STEP_DIR/headers.txt" --output "$MANAGER_STEP_DIR/operation.json"
```

不带 body/query/force。继续查询 operation 和 workspace：确认 `succeeded`、`deletion_marked=true`、资源已标记 deleting；`physical_reclaimed=false` 必须原样保留。Busy/冲突不是强制删除许可。响应丢失沿用原删除路径和 key。

源 workspace 的逻辑删除可以与其他 workspace/snapshot 引用共享 lower 并存；物理回收的引用保护是另一个边界。Manager 不提供卷删除或对象回收。云清理仍按本轮精确清单、全部安全门和释放查询单独执行。

## 3. C1/C4 计时与交付

| 阶段 | 接入位置与完成条件 |
|---|---|
| C1 环境准备 | 先能力检查，正式格式不兼容就保留原生路径；创建在负载计时外。真正挂载与读写探测全部完成后才参加 cohort 屏障 |
| C1 收尾 | 结果保全 → 停写/排空 → 卸载/释放 lease → 必要的逻辑删除；Manager 产品资源单独核算，JuiceFS 不安装无关服务 |
| C4 F | 当前使用兼容的原生 fork/clone。未来兼容 Manager 路径须重新冻结协议，分别记录 API 接收、全部元数据创建、首用及远端完成；与 JuiceFS clone 比较相同完成边界，不比较 202 与完成返回 |
| C4 M | 继续 [迁移步骤](MIGRATION.zh.md) 的应用 checkpoint、原生 drain/卸载、目标恢复；Manager 不承担尚未实现的 handoff，仍需 BrewFS/JuiceFS 各自 stay/move 资格 |

交付请求清单/请求 SHA/固定 key、每次响应、operation 终态、逐资源核对、lease/drain/卸载及保全证据，凭据单独保存。每步由监督 Agent 检查再推进；准备文档、合并 PR 或本地单元测试均不把 readiness 改为 ready，不触发实验。
