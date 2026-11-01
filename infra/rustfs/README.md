# RustFS 后端准备

阿里云、已有服务器和 SSH 容器使用同一套步骤。只跟随 [Ivanbeethoven/rustfs](https://github.com/Ivanbeethoven/rustfs) 的 `feat/dynamic-block-size-rebased` 分支最新代码。每批构建前同步；提交号和镜像 digest 记录本批实际产物，同批对照共用，下批重新同步。

| 工具 | 职责 |
|---|---|
| [resolve_backend.py](resolve_backend.py) | source 更新分支；image 核最新源码与镜像来源，保存版本回执 |
| [generate_rustfs.py](generate_rustfs.py) | 校验 spec、生成逐节点配置；默认启用 dynamic |
| [rustfs_node.py](rustfs_node.py) | 核磁盘、挂载、凭据和 Compose；显式 --start 才启动 |
| [s3_probe.py](s3_probe.py) | 临时独占桶的 PUT/GET/Range/LIST 检查和清理 |

## 1. 同步、构建、记录

首次准备专用 checkout：

```bash
git clone --single-branch --branch feat/dynamic-block-size-rebased \
  https://github.com/Ivanbeethoven/rustfs "$RUSTFS_SOURCE"
```

每批先同步，再按该分支的构建入口构建一次。镜像保留构建日志，并设置 `org.opencontainers.image.revision` 为实际源码 SHA。下列命令分别执行，中间由执行 Agent 完成构建与检查：

```bash
python3 infra/rustfs/resolve_backend.py source \
  --rustfs-source "$RUSTFS_SOURCE" --output "$RUN/source-version.json"
# 在上述源码上构建并发布镜像；记录工具链、features 和构建日志。
python3 infra/rustfs/resolve_backend.py image \
  --rustfs-source "$RUSTFS_SOURCE" --brewfs-source "$BREWFS_SOURCE" \
  --brewfs-binary "$BREWFS" --image "$IMAGE" --output "$RUN/backend-version.json"
```

source 只快进指定分支，保留本地改动和分叉提交；image 重新核远端分支，拒绝旧源码或旧镜像。回执中的 `source_commit` 是版本记录，不提供指定历史 SHA 的参数。OCI 标签与构建日志共同记录来源；运行时另核实际镜像 ID。旧 [freeze_backend.py](freeze_backend.py) 兼容 image 命令，历史回执不改写。

## 2. 默认开启 dynamic

所有节点使用 `layout_profile=enabled`，生成器默认写入：

```text
RUSTFS_EC_BLOCK_SIZE_HINT_ENABLE=true
RUSTFS_EC_BLOCK_SIZE_HINT_FLEET_CONFIRMED=true
RUSTFS_EC_READ_QUANTUM_ENABLE=true
RUSTFS_EC_READ_QUANTUM_FLEET_CONFIRMED=true
```

部署前确认全体服务节点都是指定分支的对应构建。BrewFS 使用 [build-client.sh](../../experiments/retest-2026-10-29/scripts/build-client.sh) 编译 `rustfs-ec-block-size-hint` feature，挂载适配器默认传 `--s3-rustfs-ec-block-size-hint auto`。原生导入、lower 构建与 Pack 发布也要单独核上传者的实际 hint；只读挂载不会改变已有对象布局。JuiceFS 保持自身上传行为，两者共用相同 RustFS 开关。

**当前 auto 尚未实现完整多档自动选择。** 2026-11-01 核对 BrewFS main 的[选择函数](https://github.com/Ivanbeethoven/brewfs/blob/main/src/cadapter/s3.rs)与[测试记录](https://github.com/Ivanbeethoven/brewfs/blob/main/doc/testing/rustfs-auto-object-hint.md)：

| 最终上传对象大小（压缩、封装后） | auto 行为 |
|---|---|
| ≤1 MiB | 不发送 hint，保留 RustFS 默认/内联策略；并非所有这些对象都会内联 |
| >1 MiB | 发送 1 MiB hint |

64 KiB、256 KiB、1 MiB、4 MiB 的显式选择已经支持。按对象大小或访问模式自动选择多个档位的策略仍待实现和验证；开启开关不代表多档自适应已生效。该 hint 不改变 BrewFS 的逻辑数据块边界。上述 auto 规则是本次源码观测，下批同步后重新核验。

C3 在同一个 enabled 服务端比较无 hint 与四个固定档位。显式服务端消融使用 `layout_profile=disabled|block-only` 并标记 `dynamic_ablation=true`（历史 D 阶段也接受）；客户端消融用 `prepare mount --dynamic-block off`。配置中 hint 留空；不能同时配置 auto 又声明 off。配对结果使用独立实验 ID。

## 3. 生成与启动服务

按 [SSH 运行入口](../../experiments/retest-2026-10-29/runbook/SSH-RUN.zh.md) 确认本批实际资源。填写 [spec 模板](rustfs-smoke.example.json)：私网地址、数据挂载、日志目录和已核验镜像 digest。mode 支持 smoke（1节点1盘）、single-node-four-disk、distributed（4节点各4盘）；四目录不等于四独立设备。实际资源配额按本批设计填写并核验。

凭据放各节点 `/etc/agent-experiment/rustfs.env`，权限0600，键为 `RUSTFS_ACCESS_KEY`、`RUSTFS_SECRET_KEY`。数据与日志目录预先归UID10001，脚本不格式化或修改属主。凭据不进入 spec、日志或 Git。

```bash
python3 infra/rustfs/generate_rustfs.py rustfs-spec.json generated-rustfs
# 将每个节点的目录分发到相应节点后，先检查，再单独启动。
python3 infra/rustfs/rustfs_node.py /opt/agent-experiment/rustfs1
python3 infra/rustfs/rustfs_node.py /opt/agent-experiment/rustfs1 --start
```

生成器拒绝占位镜像、重复节点、重叠或非法路径和缺失 digest；节点检查独立挂载、权限与 Compose。每个节点启动后核私网 health、实际运行镜像、四个开关及 EC 拓扑。只输出非密钥字段，不导出完整容器 Env。

## 4. 验收实际布局

用环境提供 S3 凭据，再执行独占临时桶探测：

```bash
python3 infra/rustfs/s3_probe.py --endpoint "$ENDPOINT"
```

该探测仅验 API。布局资格另记录最终对象字节数、请求 hint、HEAD 的 `x-rustfs-layout-hint-status`/reason/`x-rustfs-effective-ec-block-size`、每个 shard 的持久化 B/q、inline/external 和完整读回；工具见[布局解析器](../../experiments/retest-2026-10-29/scripts/inspect-layout.py)。客户端配置、hint 接受、布局变化和性能收益分别验收。

EC2+2 下当前 1 MiB 布局的 q 仍为512 KiB，因此 auto 开启不代表读帧缩小。C3 记录各固定档位的实际 B/q，再确定多档策略；重新挂载不会重写旧对象。新条件使用独立对象前缀，保留失败与慢值。默认 enabled 的结果与此前 disabled 结果分开统计。
