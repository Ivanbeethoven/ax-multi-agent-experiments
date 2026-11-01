# 本轮辅助脚本

当前设计见 [主方案](../PLAN.zh.md)，操作顺序见 [SSH运行入口](../runbook/SSH-RUN.zh.md)。本目录保留可复用工具及历史资格脚本。

`restore-runtime.py`、`local-ml-qualification.py`、`dynamic-probe.py` 绑定旧归档/构建，用于解释历史证据。新批次的RustFS版本通过 [resolve_backend](../../../infra/rustfs/README.md) 跟随指定分支最新代码；不从这些旧脚本恢复后端。默认dynamic开启，BrewFS auto的多档选择限制在同页说明。

| 脚本 | 作用 |
|---|---|
| [build-client.sh](build-client.sh) | 固定源码导出并构建一次，包含observability；只复用源码/工具链/features/profile均相同且核SHA的产物。packed fixture仅供独立资格，不能代替C1生产native lower |
| [capture-build-receipt.py](capture-build-receipt.py) | 逐源文件核 frozen archive，核二进制与构建记录 |
| [restore-runtime.py](restore-runtime.py) | 从保留包恢复核验过的 RustFS/JuiceFS/回放工具，不复用旧 BrewFS |
| [prepare-ml-corpus.py](prepare-ml-corpus.py) | 固定 TinyStories 下载、记录拆分、manifest；明确披露任何复制扩展 |
| [local-ml-qualification.py](local-ml-qualification.py) | 自有本地 RustFS/PD/TiKV 的 P1/P2 路径资格检查与清理；非正式性能 |
| [dynamic-probe.py](dynamic-probe.py) | 源码/镜像 pin、布局与客户端上传资格探针；同盘四目录只证明布局/内容 |
| [inline-boundary-check.py](inline-boundary-check.py) | 各 profile / versioning / 对象大小的内联边界 |
| [client-hint-check.py](client-hint-check.py) | 真实 BrewFS 上传、落盘 hint、后端重启和重新挂载读回 |
| [inspect-layout.py](inspect-layout.py) | 四 shard 的 xl.meta CRC、inline、分片与 B/q 解析 |

`--help` 用于核对参数，不自动补默认机器、数据目录或凭据。原始运行记录中旧脚本路径保留历史含义；新命令使用本目录。
