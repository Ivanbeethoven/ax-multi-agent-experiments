# 最新BrewFS的10万文件Packed本机检查

BrewFS `ca07159f3da4b670e4aeb878faef928e69bc1634`，RustFS `f4ed202508f2153afb7956bae6f7799b60eccd00`。运行脚本为`scripts/qualify-c3-packed-local.py`；完整命令、脚本副本及各阶段输出保存在原始包。

输入100,000个4KiB文件，共409,600,000B。以下均通过：

1. 独立Rust工具在导入前后完整核对输入。
2. Packed导入并通过FUSE完整读回全部基线文件。
3. 按冻结plan执行八阶段，20,224次读、768次写，应用读82,837,504B、写3,145,728B。
4. 原挂载正常退出。只读核验器从原namespace的远端状态读回全部100,000个文件及101,025项目录记录，与独立预期清单一致；元数据核验前后未变，只读账号写入被拒绝。

构建231.223s、基线完整读回625.781s、八阶段37.774s、远端完整核验854.838s。这些是本机功能检查时间：服务共用一块物理盘，而且负载前完整读回已预热缓存，不能作为云上冷缓存性能结果，也没有完成监控校准或父namespace隔离验证。

构建中209s的进程I/O快照保留在原始包：`rchar≈56GB`、`wchar≈8GB`包含临时文件和套接字访问，不是物理磁盘读写量，也不能用来计算源文件重复读取比例。一次性构建成本与后续共享运行成本分别统计。

原始包在`/home/hxy/ax-unified-pilot-prep-next-01/retention/functional-ca07159-02/functional.tar.gz`，独立副本在`C:/Users/HXY/.cache/ax-pilot-20261111/retained-functional-ca07159-02/`。10,384,832B，SHA256 `f961be32ec5c08c70d9c5c8a156dc09a0cccdd1422b31f349935c17a9c1c6f9d`，305个成员全部独立核验。包内含完整目录清单、延迟CSV、日志、原脚本及native路线复查，不含缓存和可再生成的输入文件。
