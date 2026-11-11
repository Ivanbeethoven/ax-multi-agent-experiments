# BrewFS 正常重挂后的写入失败

BrewFS `main` 的 `622736b0a31caaa9007445886e626a2458bc316a`，二进制 SHA256 为 `98e3c0402b735830efab7c80e50d8353625b3f8cecc5343ee15c37a6a761a526`。使用原构建产物，没有修改 BrewFS。

本机 WSL、真实 FUSE、Redis 和指定分支 RustFS；RustFS 的四个目录位于同一块盘。此项只检查功能，不提供云上性能结论，不调用模型。

## 已复现的步骤

1. 创建新的 `workspace-native-v2` 工作区，写入文件、重命名、创建软链接并持久化。
2. 正常卸载，原进程退出码为 0；检查租约已释放、待上传字节为 0。
3. 用空缓存重新挂载同一工作区。完整目录树、文件字节和链接检查通过。
4. 修改已有文件，再调用 `fsync`：返回 `EIO`（错误码 5）。后续新文件写入和第三次冷读回没有执行。

两次独立新工作区都复现。第二次的[重挂后完整读回](native-clean-reopened-write-02/readback-2.json)、[失败结果](native-clean-reopened-write-02/result.json)、[原挂载退出](native-clean-reopened-write-02/mount-process-1/exit.json)、[原租约释放](native-clean-reopened-write-02/inspect-1/stdout)及[失败后的收尾](native-clean-reopened-write-02/operator-closure.json)已保留。

## 复现命令

在已运行本机 Redis/RustFS 的 Linux 测试环境，以 root 执行。`BUILD` 为构建目录，`OUT` 必须是新目录：

```bash
"$BUILD/venv/bin/python" \
  experiments/unified-suite/pilot-aliyun/scripts/qualify-native-local.py \
  --route workspace --mutate-reopened \
  --build-root "$BUILD" \
  --services "$BUILD/qualification/local-services-03" \
  --out "$OUT"
```

失败后保留挂载供检查；确认原进程身份后正常卸载。不要在同一输出目录重跑。

## 对 C4 的影响

当前路线不能通过“重挂后继续写入”的前置检查，不能把先前只读重挂成功当作 Agent 恢复成功。BrewFS 的 C4 原生工作区路线暂不准入性能比较，JuiceFS 对照继续准备。

另一个[旧写者测试](native-writer-fencing-03/result.json)证实活跃第二写者被拒绝，租约过期后新写者可挂载，但新写者写入也返回 EIO，尚未执行旧句柄的写入攻击。因此不宣称已验证旧写者拒写。更早两次控制脚本失败也保留，分别为暂停时继承文件句柄、缺少本机 Redis CLI；它们没有执行接管写入。所有相关本机挂载和进程已收尾。[汇总及原始包摘要](summary.json)。
