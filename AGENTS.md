# 实验部署约定

- 用户指定：后续实验部署 RustFS 使用 `https://github.com/Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased` 分支。
- 每批开始解析分支并冻结完整提交 SHA；从该提交构建镜像，或使用来源已验证的对应镜像。记录仓库、分支、源码 SHA 和镜像 digest，单独记录 BrewFS 客户端版本。
- 同一批对照保持 RustFS 版本一致；分支或构建不可用时报告问题，不自动换成其他分支或官方 `latest`。
- 历史实验保留实际使用的版本与结果。部署入口见 `infra/rustfs/README.md`。

## 构建分发与远程访问（用户要求）

1. **尽量只编译一次，再通过内网分发。** 同一冻结源码、工具链、feature/profile 和目标平台的每个组件只构建一套产物；测试节点复用经 SHA256 / 镜像 digest 核验的二进制和镜像，不逐台重复编译。跨节点优先私网传输或同地域 OSS internal endpoint，避免公网及本机中转。构建参数不同的产物须分别标识，不能冒用同一版本。
2. **尽量只保留一个公网 IP，使用跳板机连接其他节点。** 默认仅管理/跳板节点有公网入口，其余节点只配私网地址；SSH 通过跳板机（如 ProxyJump）连接，业务、资产和结果传输尽量走内网。初始化依赖使用预制镜像、内网源或跳板代理；不能因为临时下载就给每个 worker 配公网。记录跳板与私网拓扑、来源受限的访问规则及本轮密钥归属。已有运行任务不为收敛公网入口而贸然重启或中断，后续新节点按此默认创建。

## 怎么算「钉住了」（缺一条就不算）

1. 每批开跑前 `git ls-remote https://github.com/Ivanbeethoven/rustfs refs/heads/feat/dynamic-block-size-rebased`，把结果写进本批的 `backend-pin.json`。
2. `backend-pin.json` 至少要包含：`repository`、`branch`、`frozen_commit`、`image_reference`、`image_digest`、`built_from_commit`、`resolved_at`、`brewfs_client_sha`、`resolved_by`。
3. **源码 SHA 与镜像必须机械对上**，不能靠"我们说是这个分支"：
   `docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' <image>` 必须等于 `frozen_commit`。
   镜像 digest 固定但来源不可考时，等于没有固定。
4. 部署生成的 spec 里出现 `REPLACE_WITH` 或 `REGISTRY/` 占位，生成器必须**直接失败**。
   占位符跑到节点上，就是静默换成了别的后端。
5. 记录**运行时实际生效**的 block size 与布局，不能只记录配置值；dynamic block 只有在实际布局变了才算数。
6. 分支读不到、构建失败、镜像来源对不上 —— 三种情况都**阻断本批**，在 `backend-pin.json` 里写 `blocked_reason`，
   **不自动换分支、不自动退回官方 `latest`**。
7. 历史批次保留当时的真实版本与结果，不回填新 SHA。

历史 `paper-design/design.json` 已收入 `archive/legacy-code-20261029.zip`；其中 `observed_commit` 只是 2026-10-27 的观测，**不是 pin**。每批的 pin 是本批 `backend-pin.json` 的 `frozen_commit`。
