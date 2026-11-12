# 旧 On1 采样盘退役

2026-11-12，北京时间。A 池 objects 的旧 On1 专用盘 `d-bp16jaiu2fcmbij8mg7i`（100 GiB）已分离并删除。16:49:32 查询为 `TotalCount=0`，16:50:09 独立查询再次确认该盘不存在，六台工作节点与跳板均为 `Stopped / StopCharging`。此次只处理该盘，旧套件的验收计数不变。

| 只读清点结果 | 保留位置及核对方式 |
|---|---|
| 原先被拒收的 tar，379,864,420 B | 下载到本机，SHA-256 与盘上原件一致。原失败仍保留。 |
| 已有盘外副本的 3 份大文件 | 在旧盘上实读并校验折叠流、拆分后的 observer 包和小索引包；长度与 SHA-256 均匹配[原归档回执](../c4-observer-fold-real-03/historical-objects-pack/objects-pack.json)和[副本核验](../c4-observer-fold-real-03/backups-verified.json)。 |
| 其余 24 份普通文件，共 28,656,915 B | 打包为 4,826,873 B 的 `extra.tar.gz`。从本机压缩包逐项解包校验，路径、大小和 SHA-256 均匹配。 |

全部 28 份普通文件均有保留副本。旧 `observer.sock` 是 socket，不入包。[盘面清单](inventory.json)记录只读挂载和原盘身份，[补充包清单](extra.json)记录逐文件校验值，[本机核验](local-readback.json)记录两份原始包的 SHA-256。原始包 `c4-a-flat-on1-objects.tar.gz` 和 `extra.tar.gz` 只保存在本机 `%USERPROFILE%/.cache/ax-pilot-20261111/retire-on1-scratch-20261112T084412Z/`。原 On1 三角色的修复包另有 [OSS 独立读回](../c4-observer-fold-real-03/recovered-01/publication-01/README.zh.md)。

删除顺序：[卸载](unmounted.json) → [分离](api-detach.json) → [删除](api-delete.json) → [按盘 ID 查无结果](api-disk-absent.json)。[独立复核](independent-closure.json)及[两轮七台实例状态](instance-stop-checks.json)保留了 API 请求号。复用入口为 [retire-on1-scratch.ps1](../../../scripts/retire-on1-scratch.ps1)。
