# C1：固定 64 KiB 块，只开关小读帧

在 rbm 复用本机缓存的 RustFS `f4ed2025` 镜像。两臂用同一镜像和 ELF、同一份 4 MiB 数据和 64 KiB 写入提示；每臂使用独立容器、网络和对象桶。两组都开启块大小提示，仅切换小读帧的两个开关。

| 模式 | 四个分片落盘的块大小 B | 小读帧 q | 完整 / Range GET | 客户端收到的数据 |
| --- | ---: | ---: | ---: | ---: |
| block-only | 64 KiB | 无标记 | 1 / 5 | 4,214,784 B |
| enabled | 64 KiB | 16 KiB | 1 / 5 | 4,214,784 B |

四个分片均通过 XL2 校验，数据均为外置分片。两组完整读回的 SHA-256 一致，五次 4 KiB Range GET 的内容、状态码和字节范围均正确。这里记录的是 HTTP 响应正文和持久化布局，未测后端从磁盘实际读取了多少字节，也没有测 FUSE 或 Agent 性能。四个分片在同一台主机的四个临时目录，不代表独立磁盘。

[原始采集](raw/capture.json) · [离线核验](verified.json) · [校验清单](SHA256SUMS) · [采集与核验脚本](../../../scripts/probe-c1-rustfs-quantum-local.py)。两臂的原始 S3 记录和四份 XL2 都在 `raw/`。采集时的脚本 SHA-256 为 `313da4af2fdf872249a28ba19325d4307719aa60c2e8a3e06ca138a2e32ccedc`；入库版只修正离线审计中 q 标记的字节类型，未重跑采集。自建容器和数据目录已清理，未启用阿里云或本机 Docker，未重新编译。

从仓库根目录离线复核（Python 3、`msgpack==1.1.2`、`xxhash==3.6.0`）：

```bash
P=experiments/unified-suite/pilot-aliyun
python3 "$P/scripts/probe-c1-rustfs-quantum-local.py" audit \
  --capture "$P/evidence/preparation/c1-rbm-rustfs-quantum-01/raw" \
  --decoder experiments/retest-2026-10-29/scripts/inspect-layout.py \
  --out /tmp/c1-rustfs-quantum-verified.json
```
