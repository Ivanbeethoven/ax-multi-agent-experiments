# BrewFS 资格验证补丁

以下补丁分别应用到干净的 BrewFS `main` 提交 `ca07159f3da4b670e4aeb878faef928e69bc1634`。每个补丁独立使用，不叠加；三份均通过 `git apply --check`。这些是资格验证源码，未合入 BrewFS `main`，不能当作原版构建或作为性能结果。

| 文件 | 用途 | SHA256 |
| --- | --- | --- |
| [原生 Packed fork](brewfs-c4-packed-native-fork.patch) | 从已核验的 Packed 版本做原生 fork，确认子挂载仍能访问同一份 lower | `9cc8f351749eec03e5dcb8235556a7665011e738670cae0b79fc37f52d94adda` |
| [热 lower 和只读区间](ax-brewfs-c2-hot-and-read-extent-ca07159.patch) | 数据按字节预算驻留，对照空 upper 的数据区间查询 | `1ca4440af71d3da2041ce2c7cf6a51378d11559cc6d31cd451423ccb702e7fa9` |
| [热 lower 和只读命名空间](ax-brewfs-c2-hot-read-namespace-ca07159.patch) | 保留上一项，同时增加路径查找、非根 inode 属性和目录打开的对照；两臂共用相同的权限、租约和 FUSE 处理 | `a34cb1ba0c94887368eaec85ae855d4766a39e29f38a023200525cf72fac2b40` |

准备运行前固定产物 SHA，并按各自的本机脚本重新验收。只读候选不代表关闭全部 Overlay，也不能衡量首次写入或写时复制。
