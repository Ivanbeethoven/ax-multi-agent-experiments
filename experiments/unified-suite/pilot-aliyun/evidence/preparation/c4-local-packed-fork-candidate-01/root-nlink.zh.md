# C4 Packed 导入：根目录链接数

[首次实测](smoke-receipt.json)读回了父工作区的两个文件，哈希一致。发布用于原生派生的 Packed 版本时退出 1，尚未创建子工作区。

这棵树只有一个直属子目录 `sub`。原导入路径先把原生根目录（inode 1）的链接数写成 2，再挂接 Packed 清单。发布时，冻结读取优先取得这个原生 inode，枚举目录时又会看到 Packed 中的 `sub`，因此按 `2 + 子目录数` 算出的 3 与记录里的 2 不符。首次实测没有采集 `stat` 的链接数；这两个值来自固定输入及代码路径。

- [原导入调用](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cli.rs)（`init_packed_volume`）和[元数据初始化](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/workspace_overlay/stores/kv_store.rs)（`create_volume_root_inner`、`install_packed_lower_binding`）。
- [冻结源读取和链接数校验](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/workspace_overlay/packed_v3/wire005/publication/native_effective.rs)（`inode`、`capture_directory`）。

[完整补丁](brewfs-c4-packed-native-fork-root-nlink-ca07159.patch)在首次候选版基础上，让新导入从已验证的 Packed 清单取得根目录链接数。原生根目录及其封存哈希在创建时一并写入，发布校验不变。补丁包含一个两文件、一子目录的回归测试，检查持久化根目录的链接数为 3，封存哈希与记录相符。

基线 `ca07159f3da4b670e4aeb878faef928e69bc1634`，完整补丁 SHA-256 `4464b9aaeb287d31c0dd6b8f2fcfdef02193e16d0e85e193b913f86acc1177a2`。已通过语法和应用检查；补丁编译及实际派生待验证。
