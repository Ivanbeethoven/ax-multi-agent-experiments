# Manager：C1/C4 接入与完成判据

本页只准备接入，不授权部署或开跑。2026-11-01 已按 [BrewFS PR #394](https://github.com/brewfs/brewfs/pull/394) 的合并提交 **`cba857b567fa6e94d50edd1d2e1c47c25c7b2e50`** 更新；当前证据与阻塞见 [#394 接入核对](../evidence/manager-pr394-integration-review.zh.md)。[#390 审查](../evidence/manager-pr390-review.zh.md) 保留为历史，不再把 SQLite-only 当成当前限制。[接口设计](../CONTROL-PLANE.zh.md) 中的 P2/P3 仍不是可调用合同。

执行时分别冻结 Manager 和客户端的 source SHA、feature/profile、平台、binary SHA256/镜像 digest，以及实际 capabilities。API 的 `version` 是包版本，不能代替源码 SHA。默认选上述合并提交；若选后续版本，重新核对差异与资格，不能继承旧版本成绩。

## 1. 实际能力与接入选择

| 项目 | #394 已实现 | C1/C4 如何使用 |
|---|---|---|
| `workspace-v1` catalog | SQLite、Redis、TiKV；需要 `workspace-overlay` | 已解除共享后端 API 缺口；不改变 C1/C4 原有 native 布局来迁就接口 |
| `workspace-native-v2` catalog | Redis、TiKV；需要 `native-packed-base`；仅 `native_p1_catalog` revision | 可接入 native P1 的元数据准备，仍需非空来源与真实挂载资格 |
| 卷 | 初始化空 catalog，或核验并登记已有真实卷 | 与旧 Console `/api/volumes` 配置登记区分；初始化不产生实验语料 |
| fork/恢复 | 每请求 1–100；串行、非原子；持久请求与预分配 ID；共享后端 journal owner/session fencing | 核全部子项与完整身份；丢响应重试不换 key，已推进 head 不回退 |
| 删除 | 有效 lease 检查、逻辑标记；native 另有 publication fence 与 domain quarantine | 保留 lower/对象/恢复证据；两阶段不声称跨存储原子性 |
| 节点操作 | 无 checkpoint、mount、drain、handoff、remote-ready 证明 | 由另行完成资格的原生适配器承担；当前 C1/C4 仍未 ready |

Native P1 的 carrier catalog 仍是 `workspace-v1/schema 1`，另有 native-v2 header/control；客户端配置中的 native schema 2 不得用于改写 carrier。它不支持 native `PublishedRevision`、frozen metadata、packed catalog binding 或 `workspace-entity-v1`。C1/C4 继续不用 Pack；native 每 workspace 独立 bucket 不受支持，使用共享对象池与隔离 object keys。该版本 native 只支持 `upload_before_commit`；`commit_before_upload` 消融继续阻塞。

每批保存 `manager-integration.json`，至少记录下列字段；这是实验收据，不是 Manager API 的请求 schema：

| 记录 | 当前准备阶段的值或要求 |
|---|---|
| `reviewed_merge_sha` | `cba857b567fa6e94d50edd1d2e1c47c25c7b2e50` |
| `manager_source_sha` / `client_source_sha`、构建参数、产物 SHA256/digest | 待实际构建/分发后核验；不把旧 80ddaf7 实验产物或 PR head 测试产物冒充合并版本客户端 |
| `capabilities`、格式/后端/revision kind、拓扑/TLS/上传策略 | 待授权运行后保存原始响应；静态源码清单不能伪装成服务回执 |
| `catalog_ref`、cluster/namespace/Redis DB、volume/workspace/head/domain 身份 | 跨 Manager 与客户端核对；凭据只记录引用 |
| `integration_status` | 当前 `metadata_api_supported_in_source`；不再使用 `local_api_only` |
| `formal_c1_c4_compatible` / `execution_enabled` | 当前均 `false`，直到相应适配与资格完成；本次不修改执行状态 |
| `selected_path` / `blockers` / `qualification_receipts` | 计划的 Manager 元数据＋原生节点路径、未解决项、精确证据链接；未完成项留空/阻塞，不填 ready |

运行一个 Manager，使用本地持久 journal，不共享 SQLite journal 开多副本。共享 catalog 的永久 journal owner 与续租 session 只保护控制操作；它们不等于客户端 workspace/writer lease。替换 journal 后等待 session 到期不能取得原命名空间的所有权。

## 2. 单步调用约定

以下是执行授权后的独立命令模板，不是整轮启动脚本。`MANAGER_API`、本轮资源 ID 和路径来自执行方调度单。`MANAGER_AUTH_CONFIG` 是仅执行账户可读的 curl 配置，含既有 Console 管理员鉴权；不进入 Git、报告或请求日志。管理员 token 对配置的全部 catalog 有权限，`owner` 标签不是租户隔离。

`MANAGER_STEP_DIR` 每次请求使用独立目录，保留原始响应和 HTTP headers，不覆盖前次尝试。任何变更前保存 `request-manifest.json`：方法、完整路径、请求文件及 SHA256、`Idempotency-Key`、本轮归属、预期源/目标 ID。key 为 1–128 个可见 ASCII 字符；不得含凭据。下面的 shell 变量必须先从这些已核验记录取得。

### ① 查询能力

```bash
curl --config "$MANAGER_AUTH_CONFIG" --fail-with-body --silent --show-error \
  "$MANAGER_API/api/v1/capabilities" --output "$MANAGER_STEP_DIR/capabilities.json"
```

检查 `enabled`，并逐项匹配 `profiles[]` 中的 `volume_format`、`metadata_backends` 与 `revision_kind`。顶层 `metadata_backends` 是并集，包含 SQLite 不代表 native 支持 SQLite。native 要求 `native_p1_catalog=true`、对应 profile 的 revision kind 为 `native_p1_catalog`；v1 为 `catalog_revision`。再核 `max_fork_count=100`、`batch_execution=serial`、`batch_atomic=false`、`max_concurrent_admissions=64`、`max_pending_operations=128` 及节点能力。capability 中 `remote_ready=false` 表示服务没有提供该证明，不表示已测得所有数据未上传。能力不匹配时记录缺口，不能继续调用设计草案中的接口。

### ② 初始化或登记卷

先按冻结版本的 [产品启动说明](https://github.com/brewfs/brewfs/blob/cba857b567fa6e94d50edd1d2e1c47c25c7b2e50/doc/operations/manager.md#start-one-instance) 准备本轮 `STATE_DIR/manager-catalogs.json`、持久状态目录、单实例与鉴权；仅在取得部署授权后启动。构建 native P1 的参数为 `cargo build --locked --no-default-features --features fuse-tokio-runtime,native-packed-base`；需要 release 时另加 `--release` 并记录该 profile。每个平台/feature/profile 构建一次、内网分发并核 SHA；本页没有执行构建或启动。

服务端 Redis 引用使用 `backend=redis`、一个 `url` 或 `url_env`、`namespace`、`stable_cluster_id`；URL 只支持 `redis://` 直连数据节点，Redis TLS、Cluster、Sentinel discovery 均不支持。TiKV 引用使用 `backend=tikv`、`pd_endpoints`、`namespace`、`stable_cluster_id`；v1 可配置 TLS，native TiKV TLS 不支持。不能为了通过能力检查静默关闭既定 TLS，需匹配经批准的部署 profile。namespace/cluster ID 使用 1–128 个 ASCII 字母、数字、`.`、`_`、`-`。同一 `catalog_ref` 不混用 v1/native。

连接地址与凭据可轮换，但 backend、稳定 cluster identity、namespace、Redis DB 和真实 volume/永久 owner 必须一致；换 namespace 不是恢复。一个 namespace 只给一个生命周期控制器管理。请求不携带路径、连接 URL 或凭据。以下初始化形状用于本轮已确认空的 native Redis/TiKV 引用，引用和 owner 改为实际值：

```json
{"mode":"initialize","catalog_ref":"c1-native-p1","confirm_empty":true,"owner":"c1-qualification","volume_format":"workspace-native-v2"}
```

v1 资格用独立 catalog_ref 和 `volume_format=workspace-v1`。已有卷采用 `mode=register`，提供读取真实 header 得到的 `expected_volume_id` 和实际 `volume_format`，不带 owner/confirm_empty；native 默认不能省略格式，否则会落入 v1 路径。登记会验证真实 catalog、native 控制身份并建立 journal 所有权，不能先初始化再声称登记了旧卷。初始化严格拒绝已有数据（含未建索引的 Redis key 和 native locator），不得清空服务池以消除冲突。

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

共享后端重启恢复在后台进行，HTTP 开始监听不代表恢复完成。旧 session 未过期可能返回 `session_busy`/503；worker 每 5 秒重试、最多 8 次，仍受阻则保留 `recovery_required`，之后核旧进程并以原请求/key 续作。`failed` 要显式重提原请求。429/`overloaded` 按原请求/key 退避，拒绝不是完成回执；不得删 journal、换 key 或提高规模来绕过积压。

### ④ 从冻结 revision fork

初始化返回的 `root_revision` 是空基底，不能用来测 1M 文件或 400 GiB 的 fork。先完成该格式的非空来源制作资格，冻结语料清单、内容 hash 和对象可读证据。Manager 无导入/活跃源 checkpoint API；native 可写 head 上的数据不会自动变成 P1 catalog lower，`base_revision` 也不是该 head 的快照。现有 prepared workspace 若只给 native PublishedRevision/frozen metadata，不能直接交给本接口，需另补经资格验证的 P1 lower 路径。

初始化卷可取 `root_revision`；登记卷没有 Manager 指派的 root，须列出并检查实际 workspace 的 `base_revision`，或读取已知 layer 的 `/api/v1/volumes/$VOLUME_ID/revisions/$LAYER_ID`。v1 保存完整 `layer_id`、`sealed_version`、32 字节 `root_hash` 数组。native 保存下面完整结构（仅类型说明，不可执行 JSON）：

```text
{
  "kind": "native_p1_catalog",
  "volume_id": "实际卷 UUID",
  "storage_namespace_id": [实际 16 个字节整数],
  "catalog_revision": {
    "layer_id": "实际不可变 layer UUID",
    "sealed_version": 实际整数,
    "root_hash": [实际 32 个字节整数]
  }
}
```

完整保存服务返回对象，不合成 ID/hash，不剥离 native wrapper，不改写成 PublishedRevision。对应的非空内容、对象存储/namespace 和原始语料必须另核；“精确 revision 被接受”不能证明 payload 在远端可读。

冻结请求文件 `{"revision": <完整返回的 revision 对象>, "count": <本批数量>, "owner": <本轮 owner>}`；尖括号仅说明类型，不是可执行 JSON。可用下列独立准备命令保留 JSON 类型；`REVISION_JSON`、`FORK_COUNT`、`FORK_OWNER` 来自已冻结的调度单，输出路径不得覆盖旧请求。将生成文件 SHA 写入 request manifest 后，按步骤② POST 到 `/api/v1/volumes/$VOLUME_ID/forks`，使用该 fork 自己的固定 key。

```bash
jq --argjson count "$FORK_COUNT" --arg owner "$FORK_OWNER" \
  '{revision: ., count: $count, owner: $owner}' "$REVISION_JSON" > "$REQUEST_JSON"
```

按步骤③查询 operation，核数量、每项 `succeeded`、唯一 workspace/head ID、原 revision；分别查询 `/api/v1/volumes/$VOLUME_ID/workspaces/$WORKSPACE_ID` 并保全返回值。列表为分页接口，默认一页不能证明所有子项齐全。

部分成功、响应丢失或重启后，只对同一操作重发原始整份请求与原 key，让服务按原 ID 对账；不减 count、不换源、不新建 key 重做全批。已完成子项仍属于原批。恢复期间子 workspace 已被推进或源身份不匹配时，停在冲突/待恢复状态，不回退 head 来“修复”。

单次上限 100；128 子副本固定预先拆成 **64+64** 两份请求及各自 key，使用相同源并保存两个 operation 的关联。报告共同开始至全部 128 项完成、排队、逐批结果及失败。串行和 burst 的提交调度另冻结；burst 只表示同屏障提交，不能把服务端逐项串行称为 128 路并行，也不能把两请求称为单次原子 fork。64/128 挂载与负载能力仍需实测。

### ⑤ 映射到真实客户端并确认就绪

逐个保存 workspace inspection 的 `record`、`base_revision`、`leases`、`mount_identity` 和 native 的 `native`。核 mount_identity 中的 catalog_ref/backend/format/volume/workspace、`workspace_namespace`、16 字节 `storage_namespace_id`，及 native head/domain/epoch/generation/commit sequence；这些是身份，不是远端耐久证明。

原生挂载适配器把这些身份与单独受控的客户端 metadata 连接、native 控制连接、对象存储及 namespace 配置相匹配，再生成该冻结版本的 mount 配置。API 不返回地址/凭据/可执行 mount 命令；native `upper_bucket=null` 不能改成每子项 bucket。当前尚无已完成资格的 Manager→客户端适配，不能仅凭这些字段自动开挂载。

native 客户端还要求显式 `volume.format=workspace-native-v2`、`volume.schema_version=2`、`volume.native_control_version=2`、chunk/block size 和完整 `native_base` 配置，均按冻结版本校验；carrier catalog 仍为 v1/schema1。旧 `bench/reference/aliyun_suite.py` 硬编码 Redis/workspace-v1，不能直接复用于 native-v2/TiKV。

后续获准资格时须核真实 FUSE mount/进程/boot ID、实际生效配置、非空 lower 内容 hash、子 workspace 独立写入与 sibling 不变、卸载后冷节点重开、lease/写者身份、原生远端完成回执。只有全部目标通过才进入 C1 cohort 屏障；C4 F 分别记录元数据完成与首用/远端完成。具体缺口与出口见 [接入核对](../evidence/manager-pr394-integration-review.zh.md)。

### ⑥ 安全删除

先核本轮资源归属、完整结果及 SHA 已保全、应用停写、该格式原生 drain/远端证明、真实卸载和 lease 释放。节点失联或 lease 到期不足以证明待上传数据已保全。C4 move 不删除源 workspace 来代替写者释放。

```bash
curl --config "$MANAGER_AUTH_CONFIG" --fail-with-body --silent --show-error \
  --request DELETE "$MANAGER_API/api/v1/volumes/$VOLUME_ID/workspaces/$WORKSPACE_ID" \
  --header "Idempotency-Key: $IDEMPOTENCY_KEY" \
  --dump-header "$MANAGER_STEP_DIR/headers.txt" --output "$MANAGER_STEP_DIR/operation.json"
```

不带 body/query/force。继续查询 operation 和 workspace：确认 `succeeded`、`deletion_marked=true`、资源已标记 deleting；`physical_reclaimed=false` 必须原样保留。Busy/冲突不是强制删除许可。响应丢失沿用原删除路径和 key。

native 删除先标 catalog，再设置 native publication tombstone 与 domain quarantine；第二阶段失败时可能已标记 catalog，`recovery_required` 不代表“未删除”。保留原 ID 续作，检查 native deletion 状态，不能 reset head、删除控制行或伪造 drain。Manager 的 shutdown 只停止控制请求/worker 并释放控制 session，不会替客户端排空或释放其 lease。

源 workspace 的逻辑删除可以与其他 workspace/snapshot 引用共享 lower 并存；物理回收的引用保护是另一个边界。Manager 不提供卷删除或对象回收。云清理仍按本轮精确清单、全部安全门和释放查询单独执行。

## 3. C1/C4 计时与交付

| 阶段 | 接入位置与完成条件 |
|---|---|
| C1 环境准备 | 先 profile 检查，再由 Manager 准备兼容的共享/native P1 元数据、原生适配器完成节点阶段；完整适配未合格时保持 blocked。创建在负载计时外，挂载与读写探测全部完成后才参加 cohort 屏障 |
| C1 收尾 | 结果保全 → 停写/排空 → 卸载/释放 lease → 必要的逻辑删除；Manager 产品资源单独核算，JuiceFS 不安装无关服务 |
| C4 F | 可准备 Manager P1 fork 接入；非空 lower、节点适配、64/128 与失败恢复资格完成前不开测。分别记录 API 接收、全部元数据创建、首用及远端完成；与 JuiceFS clone/Native cp 比较相同完成边界，不比较 202 与完成返回 |
| C4 M | 继续 [迁移步骤](MIGRATION.zh.md) 的应用 checkpoint、原生 drain/卸载、目标恢复；Manager 不承担尚未实现的 handoff，仍需 BrewFS/JuiceFS 各自 stay/move 资格 |

交付请求清单/请求 SHA/固定 key、每次响应、operation 终态、逐资源核对、lease/drain/卸载及保全证据，凭据单独保存。每步由监督 Agent 检查再推进；准备文档、合并 PR 或本地单元测试均不把 readiness 改为 ready，不触发实验。

## 4. 当前门禁与后续顺序（仅计划）

1. 静态冻结 #394 与目标 profile，准备源/产物收据、namespace 归属和调用适配设计。
2. 获得独立执行授权后，先完成真实 Redis/TiKV＋RustFS＋FUSE 的单子项端到端资格；保留非空来源制作、HTTP→mount、冷重开和安全收尾证据。
3. 独立验证丢响应、进程重启、session/lease、部分 native 删除和源 head 推进；核同 key/原 ID、不增殖、不回退、不丢数据。
4. 通过后验证 64 与 128（64+64）的 serial/burst、分页全量核对、背压/排队与失败子项；再进行两节点 stay/move 与邻居隔离资格。
5. 相应资格收据齐全后更新 readiness 并申请该实验单元的执行；本次停在第 1 步，其他项均未执行。异步 native、TLS/布局不匹配等产品缺口先解决，不能用另一格式代跑。
