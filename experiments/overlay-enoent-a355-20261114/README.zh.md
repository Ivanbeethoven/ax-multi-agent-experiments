# Overlay 并发替换检查

检查两个时点：查到目录项后、读取文件属性前发生替换；Linux 已记住旧 inode，但尚未打开文件时发生替换。修复在 BrewFS，运行脚本和结果保存在本目录。

## 修复行为

- `lookup_with_attr_bytes` 发现旧 inode 已消失后，重新读取同一目录项。持续替换时最多尝试 8 次；名称仍存在但无法取得稳定结果时返回 `ESTALE`。
- Linux `getattr` 遇到已消失且没有打开句柄的 inode 时返回 `ESTALE`，由内核重新查找名称并检查权限。
- 真实删除返回 `ENOENT`，权限不足返回 `EACCES`；数据库错误原样返回。已打开的旧文件句柄仍可读取属性，链接数为 0。

正常查找成功时使用原有查询次数。租约、版本检查和元数据写回方式保持现有实现。

## 重复检查结果

每个测试函数执行 3 次。相同断言用于修改前后；测试函数内部继续遍历缓存设置、文件来源和替换方式。

| 检查 | 修改前 | 修复版 |
|---|---:|---:|
| 6 类并发替换检查，共 18 次 | 18 次复现问题 | 18/18 通过 |
| 4 类删除、权限、句柄与数据库错误对照，共 12 次 | 12/12 通过 | 12/12 通过 |

两边的 4 个测试文件内容相同，BrewFS 测试程序分别从各自源码编译。修改前版本为 main 的 `fa630c33ed9691f66b09742ae695aecb0d666a0e`。

修复代码：[BrewFS 187f62d](https://github.com/brewfs/brewfs/commit/187f62dd2dc820dfdab8148bf70419eb587befa1)。

完整单元测试使用 `cargo test --workspace --lib --bins`：

| 构建功能 | 通过 | 失败 | 项目配置中忽略 |
|---|---:|---:|---:|
| 默认功能 | 1,364 | 0 | 266 |
| 实际挂载功能 | 2,894 | 0 | 493 |

实际挂载功能为 `fuse-io-uring-runtime,workspace,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability`，关闭默认功能。4 组需要 Linux 权限的 FUSE 测试另外显式执行，每组 3 次，全部通过。

55 条构建、诊断和 CI 命令全部正常完成，包括两套 check/build/test/Clippy、格式检查、7 项 Shell 检查和两项 FUSE runtime 检查。两边各记录 78 次 Redis/FUSE 挂载，测试 Redis 均正常退出。

- [汇总结果](results.json) · [输入与源码版本](inputs.json) · [测试文件内容比较](test-content-comparison.json)
- [修改前原始输出](raw/original/) · [修复版原始输出](raw/candidate/)
- [实验仓库检查记录](validation/report.json)：仓库检查、4 个新脚本语法和文档链接通过；unittest 运行 704 项，其中 26 项跳过，失败 0 项。

在本目录重新生成汇总，输出文件使用新名称：

```powershell
python ./scripts/summarize_checks.py --original ./raw/original --candidate ./raw/candidate --out ./summary-copy.json
```

## 测试条件

| 项目 | 设置 |
|---|---|
| 主机 | A355；4 个 CPU，低优先级运行 |
| 元数据 | 各轮单独启动 Redis，端口 16379，私有网络命名空间 |
| lower | 有效 Packed 文件及索引；对象保存在临时本地目录 |
| 上层数据 | 内存块存储，用于文件属性和名称操作 |
| 缓存 | 关闭、64 MiB；FUSE 属性 TTL 为 0 |
| 客户端 | uid/gid 1000，无附加用户组，启用 `default_permissions` |
| 重复 | 每组 3 次；原版与修复版使用相同测试代码 |
| 数据记录 | 单次系统调用结果、LOOKUP/GETATTR/OPEN 顺序、Redis commandstats、完整命令输出 |

普通测试覆盖 lower/upper、替换、删除、连续替换、打开句柄、权限和数据库错误。4 组真实 FUSE 测试每次合计使用 26 个独立挂载。客户端每次只执行一次 `open` 或 `stat`，重试由 Linux 完成。

完整 CI 运行现有 SQLite 单元测试。这里的实际挂载使用 Redis。本目录记录正确性检查，性能数据见[热读结果](../overlay-lease-cache-a355-20261114/README.zh.md)。

## 复测

需要已获准使用的 A355、`/dev/fuse`、Redis、现有 Rust 工具链和完整依赖缓存。源码目录分别准备为原版加测试、修复版；两边的测试文件相同。原版提交为 `fa630c33ed9691f66b09742ae695aecb0d666a0e`。

从 Windows PowerShell 启动，参数中的路径均为 A355 上的 Linux 路径：

```powershell
$options = @(
  '--ssh', 'idcai-60525',
  '--hostname', 'yun.1617k.com',
  '--port', '60525',
  '--source', '/srv/ax-enoent-local-20261114-0840/pair-02/candidate-source',
  '--out', '/srv/ax-enoent-local-20261114-0840/recheck-01',
  '--target', '/srv/ax-enoent-local-20261114-0840/target',
  '--cargo-home', '/srv/ax-enoent-local-20261114-0840/cargo',
  '--toolchain', '/srv/ax-overlay-tax-20261113/toolchain/bin',
  '--base-commit', 'fa630c33ed9691f66b09742ae695aecb0d666a0e',
  '--role', 'candidate'
)
python ./scripts/launch.py @options
```

输出目录必须是新目录，`target` 必须是本任务专用的构建目录。脚本持有 `ax-overlay-open-diagnostic.lock`，清理该目录中的 BrewFS 包产物并重新编译，保留依赖缓存。随后执行 30 次诊断，再运行格式检查、默认功能与实际功能的 check/build/test/Clippy、Shell 测试及 FUSE runtime 检查。使用 `--role original` 时只执行修改前的重复诊断。

每条命令的退出状态和耗时在 `report.json`，详细输出在同名 `.log`。Redis 在结束时退出，挂载状态记录在 `mounts_after`。

`scripts/continue_checks.py` 可继续因资源占用而中断的 CI：要求此前 30 次诊断全部通过，并逐文件确认源码与原存档相同。已通过的命令保留原始时间和日志，只执行剩余检查。CI 使用同样的 CPU、内存和优先级限制，允许其他编译进程共存；检测到文件系统进程或性能负载时停止。
