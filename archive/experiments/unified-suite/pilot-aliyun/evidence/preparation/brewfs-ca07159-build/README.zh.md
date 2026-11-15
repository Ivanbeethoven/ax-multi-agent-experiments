# BrewFS ca07159独立构建

2026-11-11 11:47构建完成，11:48再次核对两条远端分支均匹配。BrewFS `main`为`ca07159f3da4b670e4aeb878faef928e69bc1634`；RustFS指定分支仍为`f4ed202508f2153afb7956bae6f7799b60eccd00`，复用原构建及镜像。

| 产物 | SHA256 |
|---|---|
| BrewFS | `814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0` |
| Packed producer | `aa45b1ee9640da92051ead72b17ed41836b57812e05623f860b963e09373b3ab` |
| RustFS（原产物） | `7cf928734f34be79d6a41ab280113f36c06cf74f1034b66a62a95aece12623ca` |

新构建位于`/home/hxy/ax-unified-pilot-prep-next-01`。原`/home/hxy/ax-unified-pilot-prep`的622736b源码和二进制保留，原构建缓存复制后使用，新旧源码均通过干净状态检查，见[独立检查](separate-builds.json)。本轮没有修改BrewFS产品代码。

构建命令仍为`build-local.sh brewfs`，features、工具链、源码树和准确输出见[构建前记录](attestation-start.json)、[完成记录](attestation-finished.json)及[总清单](build-manifest.json)。[核验工具重新链接](reader-link.json)到本次完成的同一BrewFS库。

此记录只证明本机产物。新版本完整FUSE资格检查正在进行，云上已有的随机写配对继续使用原冻结版本。
