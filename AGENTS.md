# 实验 Agent 入口

- 2026-11-12 用户重新授权的 `ssh rbm` 最小脚本检查已完成：26 个入口最终 21 通过、5 阻塞，见 [结果与卡点](experiments/unified-suite/pilot-aliyun/evidence/preparation/rbm-script-check-20261112/README.zh.md)。临时服务及挂载已清理，未启动阿里云机器。正式规模和历史结果未改；跨机与尚未实现的接口未验证。`--tiny` 只用于明确选择的脚本小试，不改变常规参数。

- 2026-11-12 用户已清除持续目标并要求暂停实验。只完成已开始任务的归档、按创建回执清理本任务创建的非 jump 资源，以及整理脚本和困难；不得启动新实验。jump、其证据盘及任何不能证明由本任务创建的资源保留。恢复实验须有新的用户指令及新的资源清单。

- 2026-11-12 用户明确跳过 JuiceFS 混合读写（C1 `randrw`）：本轮不再补跑或补验收，原结果保留，不改成 passed。后续工作集中 C2–C4；当前未完成项见 [清单](experiments/unified-suite/current/PENDING.zh.md)。

- 选择实验路线前先读[子项与 feature 对照表](experiments/unified-suite/current/FEATURES.zh.md)，再用同目录 design.json 冻结参数。

- 2026-11-12 收尾：本任务原 A、B 两池各 3 台实例、9 块盘均已释放。原 jump 及其 40 GiB 系统盘、100 GiB 协调盘保留，jump 为 StopCharging。未删除非本任务创建的资源，原网络与 OSS 保留。见 [A 池最终核验](experiments/unified-suite/pilot-aliyun/evidence/preparation/a-pool-retirement-01/README.zh.md)及 [B 池回执](experiments/unified-suite/pilot-aliyun/evidence/preparation/b-pool-retirement-01/README.zh.md)。旧 A/B ID 不可再派发；恢复实验须新建资源清单。用户只要本地结果，不再下载原始 trace、完整监控或运行产物包。


- 2026-11-11 当前方案：新实验从 `experiments/unified-suite/current/README.zh.md` 开始，参数见同目录 `design.json`。主候选为 workspace-v1 + Packed-v3，flat-v1 只用于 C1/C2，外部仅 JuiceFS。C4 派生只比较 BrewFS 原生 fork 与 JuiceFS 原生 clone；Overlay 运行开销另做同引擎开关，在验证 lower 热态后计时。entity-v1 和 native-v2 不进入新实验。新 suite 适配及三开关统一接口尚未完成，规划文件不可直接执行。历史回执和活动测量保持原义。

- 新 pilot 为 1 Agent、C3/C4 派生 10,000×4KiB（用户 2026-11-12 最新调整）；超出小规模时间窗口的项保存证据后跳过。正式百万文件、并发最高32，参数与资格要求以 current 为准。历史输入和结果按实际规模记录，不改写为一万文件。
- 默认一池，最多两池；总并发≤32负载槽（C1计1槽，迁移目标替换源Agent）。完整配对批次固定在同一池，各臂依次执行；一个owner持有本池准备至归档的锁。共享服务/盘/瓶颈链路的导入、归档与测量串行，详见 MAIN-SCALE.zh.md。协调者本地 state.sqlite 只记录调度，元数据主线 Redis；TiKV另立补充运行。
- 三个设计用运行配置切换，同批使用同一 BrewFS 二进制和 RustFS 镜像。新方案缺少的开关/适配必须明示，不回退到旧引擎补数。`bench.suite template --legacy-catalog` 仅用于明确选择的历史协议；旧冻结计划继续使用既有工具校验。
- 节点清单、单步执行、测量程序、部署位于 `bench/`、`bench/runtime/`、`tools/`、`infra/`。运行前读实际 plan、inventory、state 与资源账本；started 不自动重放，共享 host/disk/service 使用同一 resource ID。
- 文档按实验设置、配置、方法、指标和步骤写；共用要求写一次并链接。当前入口只写实际范围，旧硬件报价和证据放在历史/参考页。

# 实验部署约定

- 用户指定：后续实验部署 RustFS 使用 `https://github.com/Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased` 分支。
- 每次冻结运行（campaign）开始同步最新分支，固定其全部配对重复与扩展曲线的产物；升级另开运行。提交SHA、产物SHA和镜像digest记录实际版本。
- dynamic 默认开启：RustFS 四个 layout 开关为 true，BrewFS 编译 hint feature 并使用 auto。auto 当前不是完整的多档自适应策略，具体行为与验收见 `infra/rustfs/README.md`。
- 同一批对照保持 RustFS 版本一致；分支或构建不可用时报告问题，不自动换成其他分支或官方 `latest`。
- 历史实验保留实际使用的版本与结果。部署入口见 `infra/rustfs/README.md`。

## 构建分发与远程访问（用户要求）

1. **尽量只编译一次，再通过内网分发。** 同一冻结源码、工具链、feature/profile 和目标平台的每个组件只构建一套产物；测试节点复用经 SHA256 / 镜像 digest 核验的二进制和镜像，不逐台重复编译。跨节点优先私网传输或同地域 OSS internal endpoint，避免公网及本机中转。构建参数不同的产物须分别标识，不能冒用同一版本。
2. **尽量只保留一个公网 IP，使用跳板机连接其他节点。** 默认仅管理/跳板节点有公网入口，其余节点只配私网地址；SSH 通过跳板机（如 ProxyJump）连接，业务、资产和结果传输尽量走内网。初始化依赖使用预制镜像、内网源或跳板代理；不能因为临时下载就给每个 worker 配公网。记录跳板与私网拓扑、来源受限的访问规则及本轮密钥归属。已有运行任务不为收敛公网入口而贸然重启或中断，后续新节点按此默认创建。

## 版本验收（提交号只记录实际版本）

1. 每批用 `infra/rustfs/resolve_backend.py source` 同步专用 checkout，再构建；`image` 步骤重新核远端分支、源码与 OCI revision，保存 `backend-version.json`。
2. 回执记录：`repository`、`branch`、`revision_policy=branch-latest`、`source_commit`、`image_reference`、`image_digest`、`built_from_commit`、`resolved_at`、`brewfs_client_sha`、`resolved_by`。
3. **源码 SHA 与镜像必须机械对上**，不能靠"我们说是这个分支"：
   `docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' <image>` 必须等于 `source_commit`。
   镜像 digest 固定但来源不可考时，等于没有固定。
4. 部署生成的 spec 里出现 `REPLACE_WITH` 或 `REGISTRY/` 占位，生成器必须**直接失败**。
   占位符跑到节点上，就是静默换成了别的后端。
5. 记录实际运行镜像、hint 状态、对象大小、inline/external、持久化 B/q 和重挂读回。开关开启、hint 接受、布局变化分别记录。只有显式消融可以关闭 dynamic；C3 无 hint 臂也使用 enabled 服务端。
6. 分支读不到、构建失败、镜像来源对不上 —— 三种情况都**阻断本批**，在版本回执里写 `blocked_reason`，
   **不自动换分支、不自动退回官方 `latest`**。
7. 历史批次保留当时的真实版本与结果，不回填新 SHA。

同一campaign分发相同产物；下一campaign重新同步分支。冻结前若分支推进，重新构建验证；冻结后不热更新。旧回执的 `frozen_commit` 仅解释历史结果。
