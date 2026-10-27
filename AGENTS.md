# 实验部署约定

- 用户指定：后续实验部署 RustFS 使用 `https://github.com/Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased` 分支。
- 每批开始解析分支并冻结完整提交 SHA；从该提交构建镜像，或使用来源已验证的对应镜像。记录仓库、分支、源码 SHA 和镜像 digest，单独记录 BrewFS 客户端版本。
- 同一批对照保持 RustFS 版本一致；分支或构建不可用时报告问题，不自动换成其他分支或官方 `latest`。
- 历史实验保留实际使用的版本与结果。部署入口见 `infra/alicloud/deploy/README.md`。
