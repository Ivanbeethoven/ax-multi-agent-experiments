# C4 非空 Packed lower 的原生 fork：本机资格未通过

使用最新已核对的 BrewFS `ca07159`、本机 WSL FUSE、隔离的 Redis 与 RustFS。输入只有两个文件，共 77 字节。父工作区绑定 Packed lower，两个文件的长度和 SHA256 均正确。对同一不可变 revision 调用 `workspace fork` 后，子工作区有独立 upper bucket，也能挂载，但没有 Packed lower 绑定，目录中 0 个文件。**不要用这条路径上云计时。** 没有跑 JuiceFS 对照或模型；没有创建任何云资源。两次挂载正常退出，临时容器和目录已清理。

[实测回执](receipt.json) · [原执行脚本](smoke.py)。脚本使用本地 Docker 缓存的固定镜像、Linux FUSE、Python 3 和 boto3，及其 `binary_root` 下两份经 SHA 校验的二进制。运行 `sudo <装有 boto3 的 Python> smoke.py <新回执路径>`；退出码 1 表示资格未通过，详细阶段见回执。本次没有测时。

[当前源码](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/src/cli/workspace_bucket.rs#L55)中的普通 fork 没有走 Packed carrier 接口；[上游旧验证](https://github.com/brewfs/brewfs/blob/ca07159f3da4b670e4aeb878faef928e69bc1634/doc/testing/issue-382-upper-object-collision.md#L28)也记录了同样现象。接通非空 lower 的原生派生后，先重做本地子工作区读回，再跑同一输入的 BrewFS fork 与 JuiceFS clone。

候选修复从已正常卸载的源发布可派生的 Packed revision，再用 `workspace fork --require-packed-carrier` 继承 lower。尚未合入上游。[本地限额构建回执](candidate-build-02.json)显示 2 核、8 GiB、禁用 swap 的构建因内存上限退出；原二进制 SHA 未变，未运行候选的 FUSE 验证。
