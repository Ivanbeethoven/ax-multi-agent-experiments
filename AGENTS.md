# 实验部署约定

- 用户指定：后续实验部署 RustFS 使用 `https://github.com/Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased` 分支。
- 每批开始解析分支并冻结完整提交 SHA；从该提交构建镜像，或使用来源已验证的对应镜像。记录仓库、分支、源码 SHA 和镜像 digest，单独记录 BrewFS 客户端版本。
- 同一批对照保持 RustFS 版本一致；分支或构建不可用时报告问题，不自动换成其他分支或官方 `latest`。
- 历史实验保留实际使用的版本与结果。部署入口见 `infra/alicloud/deploy/README.md`。

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

`paper-design/design.json` 里的 `observed_commit` 只是 2026-10-27 的观测，**不是 pin**；每批的 pin 是本批 `backend-pin.json` 的 `frozen_commit`。
