# 阿里云按需实验环境

状态：模板已生成并做本地语法检查；没有创建云资源、启动服务或执行性能实验。当前先准备 XFS/restic、JuiceFS、CephFS、ESSD 换挂；BrewFS 暂缓。Terraform 负责资源，`deploy/` 负责生成服务配置，实验 harness 负责 workload 与计时，三层结果分别记录。

## 资源与比较方式

| 部分 | 模板选择 | 用途与边界 |
|---|---|---|
| 网络 | 独立 VPC、按 AZ 配置 vSwitch、成员安全组 | 仅私网，无 EIP/NAT；从已有私网入口访问，使用预装依赖和镜像的固定镜像 |
| 计算 | PostPaid、NoSpot；client/control/s3/ceph 分开配置 | 不预设具体可售实例；正式运行前冻结 CPU、内存、网络和挂盘限额 |
| 数据盘 | 分离的 PayAsYouGo `cloud_essd` PL1 | 按需创建，可卸挂，20 GiB 起；系统盘不用于任务数据；盘挂到 ECS 不等于已建文件系统 |
| S3 | 自建 RustFS | smoke 1 节点1盘；正式模板4节点×4个独立数据盘，不使用 OSS 代替 |
| Redis | 默认不创建；可传现有私网 endpoint，或创建 Tair Redis 兼容实例 | 自建 Redis 用于同资源比较；托管实例记为 B2-M 服务/成本比较，不能称同 CPU/内存消融 |
| CephFS | 自建 CephFS＋RADOS | `ceph` 角色机器与明确 OSD 盘；NAS 不能冒称托管 CephFS |
| NAS | `enable_nas=false`，可选 General-purpose NAS/NFS | 独立 B5 托管服务参照，不并入“相同存储节点资源”结论 |

ECS 按量名为 `PostPaid`，分离盘按量名为 `PayAsYouGo`。这里固定已检查的稳定 provider **1.274.0**，不自动追随 beta；`.terraform.lock.hcl` 一起保留。参数依据 [ECS 实例](https://registry.terraform.io/providers/aliyun/alicloud/1.274.0/docs/resources/instance)、[分离盘](https://registry.terraform.io/providers/aliyun/alicloud/1.274.0/docs/resources/ecs_disk)、[盘挂载](https://registry.terraform.io/providers/aliyun/alicloud/1.274.0/docs/resources/ecs_disk_attachment)文档。

普通 ESSD 的主组安排同 AZ。**Regional ESSD 是例外**，官方支持跨 AZ 恢复/挂载路线，应做必要的跨 AZ 补充，不能以普通盘限制概括全部云盘。本模板暂未声称支持其购买参数，待核验区域、规格、API/provider 后另加，按不同产品记录成本与恢复语义。[Regional ESSD 官方说明](https://www.alibabacloud.com/help/en/ecs/user-guide/regional-essd-disks)

## 从计划到机器

1. 复制 `smoke.tfvars.example` 为不提交的 `terraform.tfvars`，填实际 region/AZ、固定 image ID、可售实例和已有 SSH key 名。示例只配 2 client＋1 control＋1 s3，**不是正式公平资源配置**。正式场景通过 `nodes`、`data_disks` 明确列出4个 S3 节点每节点4盘及所有对照资源，不同时运行互相干扰的存储组。
2. 使用已有凭据配置，或在进程环境设置 `ALIBABA_CLOUD_ACCESS_KEY_ID`、`ALIBABA_CLOUD_ACCESS_KEY_SECRET`、可选 `ALIBABA_CLOUD_SECURITY_TOKEN`。不要把密钥写入 tfvars 或 cloud-init。[Provider 身份配置](https://registry.terraform.io/providers/aliyun/alicloud/1.274.0/docs)
3. 本地检查/预览：

```bash
terraform init -backend=false
terraform fmt -check -recursive
terraform validate
terraform plan -out=reviewed.tfplan
```

`plan` 需要云权限和实际参数，会读账号/区域可用性；不等于创建。模板和本轮工作不会自动 apply。执行前检查资源数、每小时费用、磁盘/实例配额、AZ 和私网到达性。实际获准启动时使用已审阅计划 `terraform apply reviewed.tfplan`，随后 `terraform output -json inventory > inventory.json`；把不含密钥的 inventory 导入运行记录。

4. 无公网也无 NAT，所以不能假设 cloud-init 能联网拉包。提前固定 Linux 镜像，内含 Docker Compose、Python3、boto3、xfsprogs、nfs/ceph 客户端、restic、JuiceFS、计量工具和已加载的 digest 镜像；或从现有私网镜像仓库分发。模板不创建额外计费出口。
5. 用云 disk ID 对照 `/dev/disk/by-id` 与 `lsblk`，只在本批新建、身份确认的空盘上建 XFS/ext4 或 Ceph OSD。这里没有自动 mkfs/zap。XFS 对照要核对 `reflink=1`，不能把普通完整复制冒充 reflink。

Terraform 保存状态，托管 Redis 的密码即使标记 sensitive 也会进入状态；使用受控加密状态位置，不提交 state/plan。`TF_VAR_redis_password` 仅在创建时注入。RustFS 密钥放节点的外部 0600 文件，完全不进入 Terraform。

## Redis 与 NAS 的准备判据

新建 Tair 时设 `create_managed_redis=true`、`redis_subnet`、实际可售 `redis_instance_class`、`redis_engine_version`，并环境注入密码；不存在所需规格/地域时用 `existing_redis_endpoint` 或 self-hosted Redis。两种来源互斥。模板采用 AOF/noeviction 配置意图，真实实例建立后核对生效值和其持久化承诺；云端拒绝或不允许查询的参数照实记录。[Tair 资源参数](https://registry.terraform.io/providers/aliyun/alicloud/1.274.0/docs/resources/kvstore_instance)

`managed-services.tfvars.example` 给出托管 Redis 的叠加参数；复制并填实际规格后与主参数文件一起传入 plan。B2-M 在测试记录中使用 `baseline_id=B2, variant=managed_redis`，与自建 Redis 的 `variant=default` 分组。B5 目前有资源模板，尚未增加 NAS 运行适配器。

B4 的专属盘设置 `data_disks.<key>.agent_id` 后会得到 `AgentID` 云标签，交接清单填写相同 `ownership_tag_key` 和 `expected_tags`。目标 ECS 的实际 ID、盘 ID、XFS UUID 和会话校验由真实资源导入；不把 Terraform 名称当设备路径。

在 JuiceFS 试验前至少用独占测试 key 前缀完成 PING、Lua/EVALSHA、MULTI/EXEC、所需命令与事务行为、超时/重试、写入后重新挂载校验，记录 Tair 实例版型、引擎、连接数、带宽、最大内存和账单。Redis 兼容不等于所有命令/持久化语义已经等同。现有实例的配额/其他负载也要记录。

启用 NAS 时设 `enable_nas=true` 与 `nas_subnet`；导出 NFS mount target，保留客户端挂载参数、计费类型、吞吐配额及容量。独立实验 VPC 采用 no_squash 以保留测试 UID 语义，不能挂入其他工作负载。它是 NAS/NFS 参照，不是 CephFS。[NAS 类型](https://help.aliyun.com/en/nas/product-overview/general-purpose-nas-file-systems)、[NFS 资源](https://registry.terraform.io/providers/aliyun/alicloud/1.274.0/docs/resources/nas_file_system)

## 运行与结束

机器就绪后按 [服务部署说明](deploy/README.md)生成并检查 RustFS/CephFS，再运行对应 baseline adapter；`terraform validate` 绝不记成服务已经可用。磁盘迁移计时由基线脚本调用真实云 API，Terraform 仅建立资源；测量期间不要另跑 apply。迁移后把实际盘目标写回配置并 refresh/reconcile，避免之后 apply 把盘迁回初始节点。

每轮导出云实例/盘属性、容器 digest、服务配置、挂载参数、资源峰值、对象或 Ceph 使用量、网络和账单。费用按本次实际资源存活时长归集，托管 Redis/NAS 单列。停止 ECS 不等于磁盘、Redis、NAS 都停止计费。结束时先把结果存出实验资源，审阅 `terraform plan -destroy`，再使用该计划释放本模块资源；外部 Redis、SSH key 和既有私网入口不归本模块删除。不得用全账号批量删除代替 run ID 范围回收。

本地验证：Terraform 1.13.5 `fmt -check` 与 `validate` 通过，provider 1.274.0 已 init。尚未执行账号 plan、apply、真实 ECS/FUSE/服务运行；配置可售性与配额仍需实账号验证。
