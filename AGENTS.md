# 实验 Agent 入口

- 2026-11-11用户最新要求：当前阿里云试跑的C3与C4派生暂用100,000×4KiB，按`experiments/unified-suite/pilot-aliyun/PILOT-100K.zh.md`显式生成新阶段。原百万文件计划/输入保留，C1/C2及活动测量不变。通用catalog默认仍为百万文件，不能用旧输入证明新规模。

- 协调者从 `experiments/unified-suite/README.md` 接入；各pool执行者从 `experiments/unified-suite/baselines/README.md` 选择JuiceFS、BrewFS native/native-workspace/entity-native/packed或消融的独立入口。C1 fio、C2 full trace、C3百万小文件、C4 fork/流转，外部baseline仅JuiceFS，native与D/P/G为内部对照/消融。共同步骤按环境→输入→冻结与领取→单步测量→证据收尾维护一份；同池一个owner，不因文档拆分重复建DB或启动任务。旧2026-10-29的C编号/证据保持原义。运行前读本批实际plan、inventory、state与资源账本。
- 方法矩阵、节点清单、单步执行、测量程序、环境准备分别位于 `bench/scale_plan.py`（`--suite-config`）、`bench/ssh_inventory.py`、`bench/runtime/`、`tools/`、`infra/`。默认 `baseline-parallel`：按product/arm显式映射到不同集群，逐cell领取/关闭，跨池按同条件/轮次汇总配对；不把整组arms锁到同池。配置见 `experiments/unified-suite/12-parallel-baselines.zh.md`。`bench/suite.py`复用既有executor，不包办整轮。状态库留在一个协调者的本地独立盘，started不自动重放；共享host/disk/service的pool用相同resource ID。
- 文档按实验设置、配置、方法、指标和步骤写；共用要求写一次并链接。当前入口只写实际范围，旧硬件报价和证据放在历史/参考页。

# 实验部署约定

- 用户指定：后续实验部署 RustFS 使用 `https://github.com/Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased` 分支。
- 每批构建前同步该分支最新代码，不指定历史 commit。提交 SHA、产物 SHA 和镜像 digest 用于记录本批实际版本；BrewFS 客户端版本单独记录。
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

同一配对批次分发同一产物；下批重新同步分支。构建或资格阶段分支推进时重新构建，不在正在测量的批次中热更新。旧回执的 `frozen_commit` 只用于解释历史结果，不能作为新批次输入。
