# 本机空版本派生检查

2026-11-11 10:19，实际最新版BrewFS二进制检查通过，未调用模型，未创建云资源。版本及命令见[intent.json](intent.json)，实际源文件已随原始包保存。

1. 在未使用的本机Redis DB4和新桶中初始化workspace-native-v2卷，取得初始空revision。
2. 原生`workspace fork`从该revision派生两个child，检查三个workspace ID和upper桶均不同、两个child的fork base与原revision完全一致。
3. 父工作区和两个child依次挂载，确认初始为空，分别写入不同的`private.txt`，文件及目录fsync后普通卸载。
4. 三个工作区各用新缓存重挂，核完整树、模式和内容，再普通卸载；检查写者租约释放、待写回字节为0。

[结果](result.json)：六次挂载均正常退出，父子及兄弟内容独立。[独立退出检查](operator-closure.json)核原PID及启动身份均已退出，挂载点消失。工具也拒绝复用非空DB和连接其他Redis主机，未清空任何DB。

复用命令：`BUILD`为已有构建目录，`BREWFS_SHA256`取本批已核构建清单；`OUT`必须是新路径。需要本机已运行的RustFS/Redis服务、可用FUSE及root权限。

```bash
sudo "$BUILD/venv/bin/python" experiments/unified-suite/pilot-aliyun/scripts/qualify-native-fork-local.py \
  --build-root "$BUILD" --services "$BUILD/qualification/local-services-03" \
  --redis-url redis://127.0.0.1:16379/4 --binary-sha256 "$BREWFS_SHA256" --out "$OUT"
```

本次DB4已使用，重跑必须另选空DB。原始包23,293B、140个文件，SHA256及独立Windows副本的逐成员核验见[retention.json](retention.json)。包包含实际执行代码、全部命令、日志、配置、读回和退出记录；未包含服务凭据。

这只验证空revision派生、私有写入和冷读回。父工作区后续修改没有被发布到新revision；未验证非空共享基线、百万文件派生、重挂后继续修改、跨机恢复或性能。此前重挂后写入EIO的结果仍然有效。
