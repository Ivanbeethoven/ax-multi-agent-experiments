# 本轮辅助脚本

当前实验取舍见 [主方案](../../../paper-design/FINAL-PLAN.zh.md)，运行资格见 [本轮记录](../README.zh.md)。这里只放本轮可复用辅助脚本；当前操作顺序见 [分步执行手册](../runbook/RUN.zh.md)；其中会管理完整本地环境的资格脚本只保留作参考。

| 脚本 | 作用 |
|---|---|
| [build-client.sh](build-client.sh) | 从冻结 Git 对象导出并构建一次；同版本已验证产物直接复用 |
| [capture-build-receipt.py](capture-build-receipt.py) | 逐源文件核 frozen archive，核二进制与构建记录 |
| [restore-runtime.py](restore-runtime.py) | 从保留包恢复核验过的 RustFS/JuiceFS/回放工具，不复用旧 BrewFS |
| [prepare-ml-corpus.py](prepare-ml-corpus.py) | 固定 TinyStories 下载、记录拆分、manifest；明确披露任何复制扩展 |
| [local-ml-qualification.py](local-ml-qualification.py) | 自有本地 RustFS/PD/TiKV 的 P1/P2 路径资格检查与清理；非正式性能 |
| [dynamic-probe.py](dynamic-probe.py) | 源码/镜像 pin、布局与客户端上传资格探针；同盘四目录只证明布局/内容 |
| [inline-boundary-check.py](inline-boundary-check.py) | 各 profile / versioning / 对象大小的内联边界 |
| [client-hint-check.py](client-hint-check.py) | 真实 BrewFS 上传、落盘 hint、后端重启和重新挂载读回 |
| [inspect-layout.py](inspect-layout.py) | 四 shard 的 xl.meta CRC、inline、分片与 B/q 解析 |

`--help` 用于核对参数，不自动补默认机器、数据目录或凭据。原始运行记录中旧脚本路径保留历史含义；新命令使用本目录。
