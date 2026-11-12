# 补齐五个脚本入口

仅做功能检查：C2 用 367 次回调的记录，C3 用 8 个文件，C4 把已有 workspace 当文件树。按照用户最新要求，C4 不启动 OpenCode，不检查对话恢复，不调用模型。正式实验规模不变。

| 入口 | 实际操作 | 必须通过的检查 |
|---|---|---|
| C2 packed-off | 保留 Packed 数据对象，把不可变元数据导入 Redis，再严格回放 | 实际发生 KV 查询；运行期没有读取 Packed 命名空间对象；最终树及冷重挂一致 |
| C3 packed-off | 同一路径，读小文件、首次私有写、再次读写 | 内容校验、冷重挂和上述元数据路径检查 |
| C2 all-off | workspace 新建无共享 lower 的卷，先完整导入初态；hint 和 RustFS 动态布局关闭 | 引擎仍为 workspace；拒绝带 Packed 绑定的卷；严格回放和冷重挂一致 |
| C4 BrewFS move | 源端关闭后，在另一台机器打开同一 workspace | 机器及启动身份不同，源端干净卸载；目标新缓存、完整树一致，4 KiB 写入、fsync、读回及删除成功 |
| C4 JuiceFS move | 同样迁移同一 JuiceFS 卷 | 同上；不把 JuiceFS 说成独占写租约系统 |

`all-off` 是原计划中的预先完整落盘：文件数据不再从共享 lower 解析，元数据保存在 KV 中。workspace 自身的 catalog 根记录和租约仍保留，不代表删除所有与层有关的内部代码。准备时间单列，不作为回放时间。

## 运行

沿用 `python3 -m bench.experiment_smoke`。公共参数见 [RBM-MINIMAL.zh.md](RBM-MINIMAL.zh.md)。所有输出目录必须是新目录。

消融额外指定同一个候选二进制、producer 和 `--brewfs-provenance build.json`：

```bash
--experiment C2-replay --mode packed-off --full-trace --trace TRACE --replay REPLAY
--experiment C3-smallfiles --mode packed-off --tiny
--experiment C2-replay --mode all-off --full-trace --trace TRACE --replay REPLAY
```

候选回执必须含源码 SHA、完整补丁 SHA、构建退出码、默认和额外 features、实际二进制 SHA，以及对应的 `packed-metadata-off` / `eager-workspace` 能力。入口校验实际文件，缺少回执或能力就拒绝派发。诊断候选不算上游已合入的功能，也不能当正式性能产物。

当前入口使用 `workspace` feature 和 `packed_snapshot_fixture`，要求重建候选及其回执。2026-11-12 的结果使用旧名称，按当时源码保留；`build-five-smoke-candidate.sh`、`write-five-smoke-provenance.py` 和 `run-rbm-script-check.sh` 固定的是该历史协议，须在 AX `86f704be` 或对应记录版本的独立 checkout 中复现，不能把旧产物交给当前入口或改写原回执。整包缓存 `7093f141` 的性能资格也不自动提供上述消融能力。

迁移增加：

```bash
--experiment C4-handoff --mode brewfs-full --placement move \
--checkpoint CHECKPOINT --prepare-timeout 180 \
--ssh-config SSH_CONFIG --target-host TARGET_ALIAS \
--target-repo TARGET_REPO --target-tools TARGET_TOOLS --target-check-timeout 900
```

JuiceFS 换为 `--mode juicefs`。`CHECKPOINT` 指向既有输入清单，其同级 `workspace/` 是输入文件树；Agent 数据库按普通文件处理。目标端需要 Python 3、FUSE、相同版本的脚本，以及与源端 SHA 一致的 `brewfs` 或 `juicefs`。无需 Agent 二进制。`/srv/ax-pilot-session` 必须未挂载且为空。

Redis/RustFS 只在 rbm 的 loopback 上监听。源端通过 SSH 为目标建立同端口的 loopback 转发，目标使用同一卷身份和后端；结束后关闭隧道。通过 stdin 传配置和校验清单，不复制 workspace，不导入会话 JSON。跨公网隧道的小试耗时不能用来比较存储性能。

`--target-check-timeout` 控制完整树核验期限，默认 300 秒，最大 900 秒。核验比较全部名称、文件 SHA256、大小、权限和符号链接；每次只保留一个目录的枚举结果，用两个线程读文件，避免同时持有大量 FUSE 目录页。外层另留 210 秒用于挂载、写入探针和清理。超时记失败，不删减校验内容。

源端排空并干净卸载后才启动目标。记录两个主机的身份、二进制 SHA、挂载耗时、完整校验耗时、目标写入及卸载结果。此检查不验证故障切换或旧写者拒绝，也不证明 Agent 恢复执行。用户取消之前产生的 OpenCode 导出失败保留原记录，不改写为文件系统成功。

## 缩小迁移输入

只检查脚本时，可以明确去掉 Agent 的依赖目录，保留任务、Git 和状态文件：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/prepare-filesystem-handoff-input.py \
  --checkpoint /input/original/checkpoint.json --out /input/fs-small \
  --omit source/config/opencode/node_modules
```

本轮这样得到 113 个条目、71 个文件、280,418 B；原输入不变。`input.json` 记录删除路径、输入哈希和实际规模。C4 传新目录的 `checkpoint.json`。这是新的最小输入，不能替代原 894 项输入的结果。

## 清理

每个入口负责卸载、退出文件系统进程、删除自己的临时 RustFS 容器、退出私有 Redis、关闭 SSH 隧道。云机只临时启用原 jump；取回小型结果后删除本轮临时密钥及 SSH 入站规则，再调用 `StopInstance --StoppedMode StopCharging`。不删除已有 jump、磁盘或其他任务的资源。
