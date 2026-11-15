# 阿里云最小成本测试点（2026-10-25 规划）

状态：规划稿。**没有创建任何云资源**；下文价格均为当日 `aliyun ecs DescribePrice` 实查值（`cn-hangzhou`，含 40 GiB 系统盘），下单前以控制台为准。

## 0. 一句话结论

一台**按量 8C32G（或抢占式同规格）+ 复用已有 150 GiB ESSD + 按流量公网 IP**，跑 6–8 小时，成本 **¥2.5–16**。这个规格只够做单机资格校准（输入/oracle/隔离/回放器），**不够**做 B0 对比、多节点或任何性能主张——理由见 §7。

## 1. 账号现状（已查）

| 项 | 实际 | 影响 |
|---|---|---|
| 账号 | `1385049235678501`（root），AK 在 `~/.aliyun`，`default`/`cn-hangzhou` | 可直接用 CLI，无需 terraform |
| 运行中 ECS | 无 | 需新建；可随时释放，无存量风险 |
| VPC | 2 个（`k8s-vpc-…`、一个空 VPC） | **不必新建 VPC**，省一层资源和时间 |
| 闲置云盘 | `d-bp1e8tbzlbszw04ri9wl`（`buck2_projects`，150 GiB `cloud_essd` **PL0**，`cn-hangzhou-h`，2026-09-30 创建，状态 `Available`） | 已空转约 25 天 ≈ **¥95 白烧**；本次可直接挂载复用，增量盘费为 0 |
| terraform | 未安装 | 仓库 `infra/alicloud` 只能作参考；本方案走 CLI/控制台 |
| 8C32G 可售性 | `ecs.e-c1m4.2xlarge` 在 `cn-hangzhou-h` **Available** | 与闲置盘同可用区，可挂 |

仓库模板 `infra/alicloud` 是**纯私网、无 EIP/NAT** 的形态，和"能在外面点开就干活"的诉求冲突；本方案不复用它，只借用它的参数口径（PostPaid、ESSD、同 AZ 挂盘、标签）。

## 2. 实查价格（`cn-hangzhou`，按量，含 40 GiB ESSD 系统盘）

| 规格 | vCPU/内存 | 按量 ¥/h | 抢占式 ¥/h |
|---|---|---|---|
| `ecs.e-c1m2.large` | 2C4G | 0.309 | — |
| `ecs.e-c1m2.xlarge` | 4C8G | 0.534 | — |
| `ecs.e-c1m4.xlarge` | 4C16G | 0.759 | — |
| **`ecs.e-c1m4.2xlarge`** | **8C32G** | **1.434** | **0.149** |
| `ecs.u1-c1m2.2xlarge` | 8C16G | 1.488 | — |

数据盘 `cloud_essd` PL0：**¥0.105 /100 GiB /h**（150 GiB ≈ ¥0.158/h）。
公网 IP 按流量：约 **¥0.80/GB**（出方向，杭州口径，下单前核对）。

## 3. 三档方案

| 档 | 形态 | 时长 | 成本 | 能拿到什么 |
|---|---|---|---|---|
| **T0 最小验证** | 1× 抢占式 8C32G + 现有 150G 盘 | 6 h | 0.149×6 + 流量≈2 GB ≈ **¥2.5** | 环境可用性、`fixtures` 生成/校验、`worker` 本机 oracle、回放器 36 次校准 |
| **T1 稳定跑** | 1× 按量 8C32G + 现有盘 | 8 h | 1.434×8 + 流量≈5 GB ≈ **¥15.5** | 同 T0，但不担心被回收；可加跑完整物化父子隔离 12 批 |
| **T2 双节点** | T1 + 1× `e-c1m2.xlarge`(4C8G) 作第二客户端 | 8 h | ≈ **¥20** | 才有资格碰"保存后另一节点读回"（B1/B2 的跨机检查） |

T2 是满足 `execution-plan.zh.md` 第 10 节第 1 步（"阿里云小规模部署 B1/B2，检查保存后能否在另一节点读回"）的**最低配置**；再往下减就只剩单机，拿不到跨节点证据。

## 4. 网络与访问

- **推荐 A：公网 IP 按流量**（`InternetChargeType=PayByTraffic`，带宽 5 Mbps）。安全组 22 端口只放当前出口 IP。传输几百 MB 结果约 ¥1–2，换来的是一路 SSH、直接拉数据、随时看进度。
- **备选 B：完全不给公网**，用 ECS 云助手 `RunCommand` 驱动（API 通道，免入方向）。省流量费，但产出受 24 KB 输出上限约束，要把结果捞出来还得加 OSS 或临时 EIP——为省 ¥2 增加一条容易翻车的链路，不值得。

## 5. 跑什么（与 `planning/test-plan-review.zh.md` §3 对齐）

单机 6–8 小时只做三件事，全部**不依赖 BrewFS 接入**：

1. **输入/mount/元数据 oracle 校准**：`harness.fixtures create/verify --profile full` 生成 F1/F2（各 1 GiB），`worker query` direct/list-first 各三次，256 正查 + 256 负查逐条比对。
2. **完整物化参考的生命周期与父子隔离**：`cp -a --reflink=never` 建父/四子，子改 1 MiB 与 128 MiB 两档，并发 1/4 × 三批 = 12 批。
3. **确定性回放器计时校准**：`tools/fuse-replay` 36 次（worker 1/4/16 × timing off/op），确认 op 计时开销 ≤10%。

判据与红线沿用审阅稿：任一 oracle 不一致就停，先修输入再说性能。

## 6. 开关清单（命令级，未执行）

```powershell
# 0) 前置：确认那块闲置盘没有要留的数据
aliyun ecs DescribeDisks --RegionId cn-hangzhou --DiskIds "['d-bp1e8tbzlbszw04ri9wl']"

# 1) 安全组：只放自己的出口 IP 进 22
aliyun ecs CreateSecurityGroup --RegionId cn-hangzhou --VpcId <现有 VPC> --SecurityGroupName osdi-smoke

# 2) 开机（抢占式；换按量则去掉 SpotStrategy 两行）
aliyun ecs RunInstances --RegionId cn-hangzhou --ImageId <固定镜像> `
  --InstanceType ecs.e-c1m4.2xlarge --ZoneId cn-hangzhou-h `
  --SpotStrategy SpotAsPriceGo --SpotDuration 0 `
  --InternetChargeType PayByTraffic --InternetMaxBandwidthOut 5 `
  --SystemDisk.Category cloud_essd --SystemDisk.Size 40 `
  --SecurityGroupId <sg> --VpcId <vpc> --Tag.1.Key Experiment --Tag.1.Value osdi-smoke

# 3) 挂现有盘
aliyun ecs AttachDisk --RegionId cn-hangzhou --DiskId d-bp1e8tbzlbszw04ri9wl `
  --InstanceId <i-xxx> --DeleteWithInstance false

# 4) 跑完：先存结果，再停机快照，最后释放（见 §8）
```

引导脚本放 `cloud-init`：装 docker、xfsprogs、python3、boto3、restic、juicefs；镜像里预置依赖，避免开机联网拉包（私网/弱网时最省事）。盘只在新实例、身份核对过的空盘上做 `mkfs.xfs -m reflink=1`。

## 7. 为什么现在不能上正式 testbed

Section 8 的形态是 2 client + 1 metadata + 4 RustFS（25 GbE、每存储节点 4 盘）：

- 25 GbE 需要 `g7ne`/`ebm` 级别实例，单价远高于 `e` 系列，粗估整套 **¥30–50/h**，**¥700–1200/天**；
- 核心 180 / 扩展 540 次任务执行，再加 192 次 A6 回放、48 次真实运行 + 8 次续跑，按这个成本是**数周到数月**、上千到上万元量级；
- 更关键的是 **B0（BrewFS）尚未接入**（`not_configured`、`run_scenario` 为空），现在把钱花在集群上，也测不出论文要的那条曲线。

所以顺序应是：单机资格（¥2.5–20）→ 接入 B0 → 再决定要不要上双节点 → 最后才谈 7 节点。

## 8. 成本守卫

1. 一切资源打 `Experiment=osdi-smoke-<日期>` 标签，回收按标签范围做，不按"整个账号"批量删。
2. 开预算提醒（¥50 阈值）+ 当天本地定时脚本检查存活实例。
3. **停机 ≠ 免费**：按量实例停机仍收云盘费；结束一律"存结果 → 快照 → `DeleteInstance` + `DeleteDisk`"，闲置盘 `d-bp1e8tbz…` 若本次不用就一并释放。
4. 抢占式实例有 5 分钟回收通知，长跑任务每 10 分钟落一次结果到盘并同步到对象存储/本地，防止白跑。

## 9. 待你拍板

1. **抢占式（¥0.15/h，可能被回收）还是按量（¥1.43/h，稳）？**
2. **复用那块闲置 150G 盘，还是新开 100G 盘？**（复用省钱，但需确认 `buck2_projects` 里没有要留的数据）
3. **公网 IP（方便）还是云助手（省流量但麻烦）？**

拍板后我可以按 §6 直接拉起来，跑 §5 的三件事，当天出结果表并释放。
