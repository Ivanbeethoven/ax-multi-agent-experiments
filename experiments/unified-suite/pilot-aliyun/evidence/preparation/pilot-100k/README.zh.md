# 10万文件输入准备

2026-11-11在本机WSL准备，未使用云上执行槽位，未调用模型。每份输入100,000个4KiB文件，共409,600,000B。

- C3：原工具完整读回通过；独立Rust工具另核全部名称、内容、权限、归属和链接。完整预期树101,025项。
- C4：完整初始树100,100项；提前生成覆盖overwrite、append、truncate、unlink、rename、create的完整child预期树。
- 草稿：C3有4项，fork有5项，文件数、输入标识和容量均按10万生成，尚未绑定云环境，不能直接执行。
- 首次并行准备的C3分支在创建脚本快照时失败，未创建输入。C4成功；C3随后使用独立记录执行成功。失败日志保留。修复后强制两个进程同时发布快照的检查通过。

入口和准确命令见[10万文件设置](../../../PILOT-100K.zh.md)，全部摘要见[result.json](result.json)。原始准备记录、完整预期树、测试输出和源码快照共95个文件，已打包并在NTFS副本逐成员核验：

```text
WSL: /home/hxy/ax-unified-pilot-prep/retention/pilot-100k-01/preparation.tar.gz
Windows: C:/Users/HXY/.cache/ax-pilot-20261111/retained-pilot-100k-01/preparation.tar.gz
SHA256: d5be0e87f143b3788cd454d525b64b5533d95190d47196c32a6c9f71a70f28d7
大小: 13,229,841 B
```

两份输入本体保留在`/home/hxy/ax-unified-pilot-prep/inputs/pilot-100k/`。上面的包包含准备证据和预期清单，不包含819,200,000B输入内容。原百万文件输入保留。云上导入、容量余量、八阶段运行和派生性能尚未验收。
