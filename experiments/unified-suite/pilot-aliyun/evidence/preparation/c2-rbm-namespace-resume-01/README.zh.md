# C2：同目录续编通过，WSL 对照未挂载

沿用[首次构建保留的同目录续编脚本](../c2-rbm-namespace-compile-01/resume-once.sh)。开跑前重新核对 BrewFS main 为 `ca07159f3da4b670e4aeb878faef928e69bc1634`，补丁 SHA-256 为 `a34cb1ba0c94887368eaec85ae855d4766a39e29f38a023200525cf72fac2b40`。C4 的构建退出并交接后，只启动一次；源代码、Cargo registry 和 target 均在原 C2 目录。没有再次复制依赖，没有启用阿里云资源。

- [构建终态](raw/terminal-observed.txt)：`ax-brewfs-c2-hot-namespace-a34cb1ba-resume-01.service`，InvocationID `202840f666e542d4926d57be165df07b`，07:38:36–07:45:45 UTC，退出码 0。峰值内存 16,031,318,016 B，CPU 累计 696.28 秒。[构建日志](raw/build-resume-01.log)只有一条 `Compiling`，对应 BrewFS 主包。
- [产物核对](raw/transport.json)：rbm、Windows 和 WSL 的二进制 SHA-256 均为 `e4c5b0b5ed88f4597f5e1d731dc4abeb305d26dc64cd4f8c489efb7b5e3fba88`。只传输了一次压缩包；首次 Windows 路径转写失败后，从已验 SHA 的本地文件复制到 WSL。
- [WSL 命令](raw/argv.json)只执行一次：`fixture.py --namespace-pair --retained-bytes 8388608 --timed-passes 128`，使用上述候选和已固定摘要的本地 Redis、RustFS 镜像。初始化产生了 Packed 父工作区、精确修订及绑定；第一组 `overlay-read` 在挂载时退出，错误为 `Packed read control requires a fresh empty upper and sealed root-only stub`。该字符串由候选的 `WorkspaceMetaLayer::enable_packed_read_control` 统一返回，[原始回执](raw/result.json)无法定位具体哪条检查未通过。
- 第一组未挂载，第二组未开始。没有取得预热计数、后端读取量或计时，也没有新增正式实验结果。[清理回执](raw/result.json)确认挂载、两只自建容器和临时目录均不存在，[独立容器查询](raw/execution.json)无遗留。

源码解释了第一组为什么过不了：`InstallPackedLowerBinding::record` 要求安装前 `head.next_sequence == 1`；Redis 安装首次绑定时调用 `allocate_layer_sequences(&mut head, 1)`，在同一事务将值更新为 2，不写入 upper 行。SQLite 同样推进一次。候选的挂载检查却继续要求安装前的 1。回执确认首次绑定已安装，但未保存 Redis 原始层记录；这是从已固定源码得出的结论。

后续构建先应用原候选补丁，再应用[顺序修正补丁](../../../patches/ax-brewfs-c2-initial-binding-sequence-followup.patch)（SHA-256 `794c14b0248e9b90c93a799b753484209a36d0cb3cdad27864c0ec288c695935`）。修正只接受首次绑定后的 `next_sequence == 2`、`binding_version == 1`，保留空 upper、根目录占位摘要和租约检查。已用原补丁的精确文件验证 `git apply --check --whitespace=error`；修正尚未编译或重跑。复验仍需两组热态及零回源检查全部通过。

[原始文件校验](SHA256SUMS)
