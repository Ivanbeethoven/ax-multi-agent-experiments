# RustFS 后端准备（阿里云与自备服务器共用）

| 工具 | 职责 |
|---|---|
| [freeze_backend.py](freeze_backend.py) | 记录指定分支、源码与镜像对应关系 |
| [generate_rustfs.py](generate_rustfs.py) | 校验 spec 并生成逐节点配置；不启动服务 |
| [rustfs_node.py](rustfs_node.py) | 检查本机磁盘、挂载、凭据和 Compose；只有显式 `--start` 才启动 |
| [s3_probe.py](s3_probe.py) | 临时独占桶上的 PUT/GET/Range/LIST 探测及清理 |

模板是 [rustfs-smoke.example.json](rustfs-smoke.example.json)。先按[操作手册](../../experiments/retest-2026-10-29/runbook/RUN.zh.md)填写本批配置，机器已创建不代表服务可用。

## RustFS

新增 `single-node-four-disk` 模式用于单机四盘诊断（1 node、4 data_mounts），不代表四节点故障域。镜像/源码冻结用 `freeze_backend.py`；测量与调度见 [运行入口](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/0c39995d793dfbf15bf142c3be3b807b31949d6c/infra/alicloud/RUN.zh.md)。

后续实验部署必须使用 [Ivanbeethoven/rustfs](https://github.com/Ivanbeethoven/rustfs) 的 `feat/dynamic-block-size-rebased` 分支：每批先解析并冻结完整提交 SHA，再从该提交构建镜像，或使用来源已验证为该提交的对应镜像。将仓库、分支、源码 SHA 和实际镜像 digest 一起记录到本批构建/部署证据；不能默认使用官方 `rustfs/rustfs:latest`，也不能只凭镜像固定了 digest 就认定它来自指定分支。

`generate_rustfs.py` 按已冻结的 image digest 生成逐节点 Compose JSON。`smoke` 为1节点1盘，只验接入；`distributed` 固定4节点、每节点4个独立文件系统。后者沿用官方多节点 host network 和 volume 展开语法；16个目录不能指向同一块盘。容器默认 UID 10001；运行前确认目录属主、实际 EC/副本、服务端布局和节点故障目标。4×4 拓扑本身不证明任何耐久性。[官方 Docker 部署](https://github.com/rustfs/docs.rustfs.com/blob/main/content/en/installation/container/docker.md)、[部署模式与冗余](https://docs.rustfs.com/en/installation)

1. 按 `rustfs-smoke.example.json` 填私网 IP、已挂载数据盘路径、日志目录，并把镜像占位替换为上述分支对应的已验证镜像地址和 digest。**来源校验先做完再填**：`docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' <image>` 必须等于本批 `frozen_commit`；对不上就阻断本批、写 `blocked_reason`，不换分支也不退回官方 `latest`。生成器遇到 `REPLACE_WITH` / `REGISTRY/` 占位必须直接失败，不能产出可运行的 spec。源码与镜像对应关系单独保存为本批证据，不向 spec 添加生成器未约定的字段。正式配置将 mode 改为 distributed，列4个 node，每个 `data_mounts` 为 `/mnt/rustfs0`…`/mnt/rustfs3`；IP和盘由本批 inventory 确认。
2. 每节点创建相同凭据的 `/etc/agent-experiment/rustfs.env`，权限0600，内容是 `RUSTFS_ACCESS_KEY=…`、`RUSTFS_SECRET_KEY=…`。值用至少12位随机字母数字；不放进 spec、tfvars、命令行或输出。数据挂载点和日志目录需已归 UID10001；脚本不会自动改目录或格式化盘。
3. 生成配置，将各节点目录发送到对应节点：

```bash
python3 generate_rustfs.py rustfs-spec.json generated-rustfs
# 在 rustfs1 对应 Linux 节点执行；其他节点对应其目录。
python3 rustfs_node.py /opt/agent-experiment/rustfs1
python3 rustfs_node.py /opt/agent-experiment/rustfs1 --start
```

生成器先校验全部节点；重复 IP、重复/相互包含的数据与日志/凭据路径、根路径、`..` 路径和未固定 digest 的镜像都会失败，参数失败时不建立输出目录。跨平台生成也按 Linux 路径解释。不要启动生成失败或手工拼接的半成品配置。

第一条节点命令仅验证本机 IP、独立挂载、UID、0600凭据文件和 Compose 语法；第二条才启动。分布式4节点使用同一 spec、相同凭据，逐节点启动后检查各私网 `http://IP:9000/health`。Console关闭，API绑定私网 IP。明文 HTTP仅用于独占私网测试；比较各方案的传输保护配置必须一致并记录。

4. 从客户端以环境变量提供 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`，运行：

```bash
python3 s3_probe.py --endpoint http://PRIVATE_S3_IP:9000
```

probe 创建随机命名的独占临时桶，检查 PUT、HEAD、完整GET、Range、LIST，再仅删除其对象和桶；成功只说明这些 API 正确。随后运行 BrewFS/JuiceFS 的真实创建、写、fsync、保存和跨客户端重开；另收集 RustFS 实际 EC/布局、GET/PUT、磁盘IO、容器CPU/内存及背景维护。未确认远端提交/冗余前，不开始耐久性等价的主比较。

服务运行期间凭据可被节点上的 Docker 管理员查看；不要导出含 Env 的完整 `docker inspect`。只导出镜像ID、资源、端口和非密钥配置。stop/down只停服务，不删除数据盘；重新初始化新一轮请使用新的独占前缀/桶。
