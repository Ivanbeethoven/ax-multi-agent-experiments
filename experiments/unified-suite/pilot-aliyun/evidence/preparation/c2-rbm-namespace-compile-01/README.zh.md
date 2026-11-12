# C2 只读命名空间候选：rbm 首次构建到达上限

候选补丁 SHA256 为 `a34cb1ba0c94887368eaec85ae855d4766a39e29f38a023200525cf72fac2b40`，基于开跑前实际核对的 BrewFS main `ca07159f3da4b670e4aeb878faef928e69bc1634`。使用一份独立 checkout、Cargo registry 和 target。C4 负责人授权复制三个依赖目录，原 C4 目录在编译服务里只读；复制后 Cargo 仍重编了部分依赖。

- [实际 systemd 回执](raw/systemd.txt)：`ax-brewfs-c2-hot-namespace-a34cb1ba-01.service`，InvocationID `ffeb7e065ad648c285991c5564c69e0d`，KST 16:06:50 启动，16:21:50 终止，**900 秒**。状态 `timeout`、信号 15、MainPID 0。上限 38 GiB、5 核、禁用 swap，实际内存峰值 6,425,989,120 B（5.98 GiB），CPU 累计 2,244.03 秒。
- [原始构建日志](raw/build.log)共 **470 条 Compiling 行**，包含 BrewFS、`aws-sdk-s3` 和 `aws-config`；这是开始编译的记录，不能当作全部编译完成。日志没有成功结束标记，也没有足以确认 Rust 编译错误的诊断。**没有候选二进制、没有 WSL 回放或计时结果**。
- 编译器已停止，rbm 已交还 C4。保留原 C2 目录 `/home/dev/ax-brewfs-c2-namespace-20261112.F2B3t2Iv`。只有 C4 完成并交还后，才考虑在同一个 source/registry/target 续一次，避免再次搬迁 Cargo 路径。当前没有再次启动，也没有启用阿里云资源。

[启动读回](raw/start-readback.txt) · [超时摘要](raw/result.json) · [首次构建脚本](guard.sh) · [SHA256](SHA256SUMS)

[同目录续跑脚本](resume-once.sh)只允许在上述原 C2 目录运行，保留首轮日志和 Cargo 路径。后续由 C4 负责人确认交接并提供实际结束的 C4 unit 和 InvocationID。另取新鲜的 BrewFS main 查询，再分别保存两个单行文件：

~~~text
resume-owner-handoff.txt: c4_handoff_path <实际 C4 .service> <实际 InvocationID>
resume-branch-tip.txt: <UTC Unix 秒> <BrewFS main commit>
~~~

把续跑脚本和两份记录分发到原 C2 目录，且不得覆盖现有文件。先在本地运行 bash resume-once.sh --preview，核对续跑 unit 为 ax-brewfs-c2-hot-namespace-a34cb1ba-resume-01.service。交接后才能在 rbm 调用 bash /home/dev/ax-brewfs-c2-namespace-20261112.F2B3t2Iv/resume-once.sh --launch。同样限制为 38 GiB、5 核、900 秒。产物和日志使用新的 resume-01 名称；没有成功产物 SHA 就不跑 WSL 计时。
