# rbm：原生 fork / clone 的脚本复验

BrewFS 与 JuiceFS 都通过。两边使用同一份非空输入：根目录文件 13 B、子目录文件 64 B，共 77 B。单台 rbm、root 身份、临时 Redis 和单节点四目录 RustFS；没有新开云机，不作性能结果。

| 检查 | BrewFS fork | JuiceFS clone |
|---|---|---|
| 子工作区首次读回两个父文件，SHA256 一致 | 通过 | 通过 |
| 子覆盖已有文件，并新建文件，写后 fsync | 通过 | 通过 |
| 重新挂载父工作区，原文件与目录不变 | 通过 | 通过 |
| 空缓存重挂子工作区，覆盖内容、新文件和未修改文件全部正确 | 通过 | 通过 |
| 四次挂载均正常卸载；自建服务、临时数据清理 | 通过 | 通过 |

BrewFS 复用了[已有修复和构建](../c4-local-packed-fork-candidate-01/root-nlink.zh.md)，没有重新编译。基线 main 为 ca07159；候选包含原生 Packed 发布、绑定继承及根目录链接数修正，二进制 SHA256 为 3ecde1a2a4a55d450b7680661c655fd8d08491afc6aebacef84b58cea76bd6ee。它是关闭 BrewFS 主包优化的功能诊断产物，不是无补丁上游或正式性能产物。

脚本以前直接从导入产生的原生占位修订 fork。现在先读取并卸载父工作区，使用同一个父配置执行 `publish-packed-snapshot`，再将返回的 `fork_source` 传给 `fork --require-packed-carrier`。同时验证：导入修订不能直接用于该 fork；发布修订独立；子继承该修订、有独立且初始为空的写入桶。原先失败回执保留。

`bench.experiment_smoke --experiment C4-fork --mode brewfs-full` 新增 `--brewfs-provenance`，在启动服务前核对补丁、源码和产物 SHA256；JuiceFS 使用 `--mode juicefs`。两边共用输入、检查及清理脚本。完整实际命令见下列入口回执的 argv。

- [BrewFS 入口与命令](brewfs/receipt.json)，[逐步结果](brewfs/run/receipt.json)。
- [JuiceFS 入口与命令](juicefs/receipt.json)，[逐步结果](juicefs/run/receipt.json)。
- [版本与同输入核对](versions.json)，[文件校验](SHA256SUMS)。

未覆盖：十万文件资格、性能、跨机续跑。父子覆盖隔离已在本机实际验证，不能继续按旧回执写成“原生 fork 子为空”。

验证：`python3 -m unittest discover -s tests -p "test_*.py"` 共 656 项，结果 OK，23 项跳过；[原始输出](unit-tests.txt)。运行脚本摘要见 [scripts.json](scripts.json)。
