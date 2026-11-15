# 环境准备

六机分工、依赖、版本与部署步骤见 [SETUP](../docs/SETUP.md)。

| 目录 | 用途 |
|---|---|
| `idcai/prepare_host.py` | Linux 依赖检查与安装、主机信息 |
| `idcai/build_release.py` | A366 限定并发构建 BrewFS，保存命令和产物信息 |
| `rustfs/generate_rustfs.py` | 生成四节点一盘等拓扑的部署配置 |
| `rustfs/rustfs_node.py` | 检查节点目录、镜像来源和服务设置 |
| `alicloud/`、`servers/` | 其它环境的参考说明 |

当前使用已有六台服务器，配置来自 `configs/cluster.json`，实际地址与目录写入本轮 inventory。运行结果放 `results/cluster-c1-c3/run-NNN/`。
