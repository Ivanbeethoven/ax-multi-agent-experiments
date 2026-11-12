# C2：同目录续编通过，WSL 对照未挂载

沿用[首次构建保留的同目录续编脚本](../c2-rbm-namespace-compile-01/resume-once.sh)。开跑前重新核对 BrewFS main 为 `ca07159f3da4b670e4aeb878faef928e69bc1634`，补丁 SHA-256 为 `a34cb1ba0c94887368eaec85ae855d4766a39e29f38a023200525cf72fac2b40`。C4 的构建退出并交接后，只启动一次；源代码、Cargo registry 和 target 均在原 C2 目录。没有再次复制依赖，没有启用阿里云资源。

- [构建终态](raw/terminal-observed.txt)：`ax-brewfs-c2-hot-namespace-a34cb1ba-resume-01.service`，InvocationID `202840f666e542d4926d57be165df07b`，07:38:36–07:45:45 UTC，退出码 0。峰值内存 16,031,318,016 B，CPU 累计 696.28 秒。[构建日志](raw/build-resume-01.log)只有一条 `Compiling`，对应 BrewFS 主包。
- [产物核对](raw/transport.json)：rbm、Windows 和 WSL 的二进制 SHA-256 均为 `e4c5b0b5ed88f4597f5e1d731dc4abeb305d26dc64cd4f8c489efb7b5e3fba88`。只传输了一次压缩包；首次 Windows 路径转写失败后，从已验 SHA 的本地文件复制到 WSL。
- [WSL 命令](raw/argv.json)只执行一次：`fixture.py --namespace-pair --retained-bytes 8388608 --timed-passes 128`，使用上述候选和已固定摘要的本地 Redis、RustFS 镜像。初始化产生了 Packed 父工作区、精确修订及绑定；第一组 `overlay-read` 在挂载时退出，错误为 `Packed read control requires a fresh empty upper and sealed root-only stub`。该字符串由候选的 `WorkspaceMetaLayer::enable_packed_read_control` 统一返回，[原始回执](raw/result.json)无法定位具体哪条检查未通过。
- 第一组未挂载，第二组未开始。没有取得预热计数、后端读取量或计时，也没有新增正式实验结果。[清理回执](raw/result.json)确认挂载、两只自建容器和临时目录均不存在，[独立容器查询](raw/execution.json)无遗留。

本次只验证了编译和错误拦截。下一步应在维持上层为空、根目录占位和租约检查的前提下，把同一错误拆成可区分的诊断，再核对测试输入实际生成的层记录。此次没有重跑。

[原始文件校验](SHA256SUMS)
