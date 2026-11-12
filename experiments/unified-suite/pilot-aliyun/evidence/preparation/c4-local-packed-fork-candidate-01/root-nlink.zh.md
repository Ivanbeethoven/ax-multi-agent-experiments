# C4 Packed 导入：根目录链接数

[首次实测](smoke-receipt.json)读回了父工作区的两个文件，哈希一致。发布用于原生派生的 Packed 版本时退出 1，尚未创建子工作区。

这棵树只有一个直属子目录 `sub`。原导入路径先把原生根目录（inode 1）的链接数写成 2，再挂接 Packed 清单。发布时，冻结读取优先取得这个原生 inode，枚举目录时又会看到 Packed 中的 `sub`，因此按 `2 + 子目录数` 算出的 3 与记录里的 2 不符。首次实测没有采集 `stat` 的链接数；这两个值来自固定输入及代码路径。

- [原导入调用](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cli.rs)（`init_packed_volume`）和[元数据初始化](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/workspace_overlay/stores/kv_store.rs)（`create_volume_root_inner`、`install_packed_lower_binding`）。
- [冻结源读取和链接数校验](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/workspace_overlay/packed_v3/wire005/publication/native_effective.rs)（`inode`、`capture_directory`）。

[完整补丁](brewfs-c4-packed-native-fork-root-nlink-ca07159.patch)在首次候选版基础上，让新导入从已验证的 Packed 清单取得根目录链接数。原生根目录及其封存哈希在创建时一并写入，发布校验不变。补丁包含一个两文件、一子目录的回归测试，检查持久化根目录的链接数为 3，封存哈希与记录相符。

基线 `ca07159f3da4b670e4aeb878faef928e69bc1634`，完整补丁 SHA-256 `4464b9aaeb287d31c0dd6b8f2fcfdef02193e16d0e85e193b913f86acc1177a2`。[新构建](root-nlink-build-receipt.json)退出 0，二进制 SHA-256 已在 WSL 核对。

[普通用户测试](root-nlink-smoke-receipt.json)发布了独立的不可变 Packed 版本，并从这个版本原生派生了两个子工作区。父工作区与首个子工作区读回两个文件，共 77 字节，哈希一致；实测根目录和 `sub` 的链接数为 3 和 2。UID/GID 1000 在子工作区根目录创建 `private.txt` 时得到 `EACCES`，因此本次没有得出写入隔离结论。

[与 JuiceFS 对照一致的 root 身份测试](root-principal-smoke-receipt.json)用同一二进制与同样的两个文件确认：六次挂载中，根目录为 `0:0`、`0755`，`sub` 保留输入的 `1000:1000`、`0755`。在第一个子工作区创建文件并 `fsync`，卸载后以新的缓存目录重挂，文件哈希一致；重新挂载兄弟和父工作区均看不到该文件。两个子工作区共享同一 Packed 版本，各有独立的写入桶。

这两次测试都只用了一个 WSL 主机与临时 Redis、RustFS；全部卸载并清理。通过的是本地原生派生及写入隔离资格，未测跨机恢复或性能。

复现：在 Linux 或 WSL 中使用本机 Docker、`/dev/fuse`、`fusermount3`，以及安装了 `boto3` 的 Python 3.11 或更新版本。Redis 和 RustFS 镜像须已在本机，镜像 ID 见结果；脚本不会拉镜像。准备好由该补丁构建的 BrewFS 及固定哈希的 `packed_v3_snapshot_fixture`。从实验仓库根目录运行：

```bash
P=experiments/unified-suite/pilot-aliyun
E="$P/evidence/preparation/c4-local-packed-fork-candidate-01"
sudo -n timeout -k 15s 180s /absolute/path/to/python3 "$P/scripts/qualify-c4-packed-fork-local.py" \
  --candidate /absolute/path/to/brewfs \
  --provenance "$E/root-nlink-build-receipt.json" \
  --producer /absolute/path/to/packed_v3_snapshot_fixture \
  --out /absolute/path/to/new-smoke.json
```

若从同一补丁重新构建，用对应的成功构建收据替换 `--provenance`，其中记录 `source_commit`、`source_tree`、`candidate_patch_sha256`、`artifact_sha256` 与 `passed: true`。脚本会在启动服务前核对二进制哈希，且只使用本机 Docker 和回环端口。
