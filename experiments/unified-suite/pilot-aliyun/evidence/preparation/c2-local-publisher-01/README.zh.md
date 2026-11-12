# C2：原初态在本地完成 Packed 构建

使用与[A 池超时运行](../c2-current-trace-a-producer-timeout-03/README.zh.md)完全相同的 producer 二进制 `aa45b1ee…`，以及原 `pytest-full-02/initial`。运行前后逐路径核对原清单：614 个文件、100 个目录、35,526,497 B，内容和模式均未改变。

在 WSL 原生盘、仅 loopback 的网络空间内运行，最多使用两个逻辑 CPU。实际 producer 在 **5.527716 秒**返回 0，生成 **182 个对象、36,960,409 B**；随后逐对象完整读回并计算 SHA-256。主清单为 wire005，内容 SHA 与对象键一致；原生输出报告 96 个 group、207 个 frame。子进程 user/system CPU 分别为 0.323/0.470 秒。这是一次本地诊断，不是性能重复试验。

- [执行回执](result.json)包含实际参数、二进制/输入 SHA、网络和 CPU 约束。
- [对象清单](objects.json)记录每个精确键、字节数和读回 SHA。
- [producer 原生结果](manifest-key.source.json)、[stdout](stdout)和[stderr](stderr)保留原始字节。

本次使用 LocalFS 后端，没有启动 FUSE、Redis、RustFS 或云机，也没有模型调用。与原 S3 命令保留相同的扫描、raw/raw 编码、inline off、4 MiB chunk/block、size-only、256 KiB 随机帧和内存预算；S3 endpoint、bucket 和 auto hint 不适用于 LocalFS。两个环境的主机、存储及源文件系统属性不同，不能用本次用时减去云端的 300 秒，也不能声称云端会生成相同的 Packed 摘要。

结果证明这份输入的扫描和编码可以完成；尚未确定原云端等待发生在何处。下一步只记录原 S3 发布的请求与进度，不重跑完整 trace，也不更改原超时结果。

可复用脚本：[diagnose-c2-local-pack.py](../../../scripts/diagnose-c2-local-pack.py)。从仓库根目录在 Linux/WSL 中执行；输出目录必须全新。

```bash
sudo unshare --net -- python3 -B \
  experiments/unified-suite/pilot-aliyun/scripts/diagnose-c2-local-pack.py \
  --producer "$PRODUCER" --case "$CASE" --out "$NEW_OUT" --seconds 60
```

只读数据及输出保存在 WSL `/home/hxy/ax-c2-local-pack-20261112-01/`。首次准备检查误把宿主 sysfs 网卡视图当成新网络空间，程序在启动 producer 前拒绝；脚本已改用内核 `if_nameindex` 查询当前网络空间。没有因此重跑一次 publisher，也没有改动原输入。
