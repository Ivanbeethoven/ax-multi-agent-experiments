# 热 lower 下的 Overlay 本地验证

已完成：两个负载各 7 对重复，28 个测量单元。两文件热读耗时降低 **35.3%**；
带上层修改和删除的热读耗时降低 **14.1%**。没有使用云机或付费服务，没有修改历史 C2 结果。

- [结果、方法和限制](RESULTS.zh.md)
- [七对汇总](evidence/summary-pooled.json)；[结果核验](evidence/verification.json)
- [前三对原始记录](evidence/run-03/run.json)；[后四对原始记录](evidence/run-04/run.json)
- [正确性检查](evidence/checks/README.md)；[构建与源码](evidence/provenance.json)

## 对照测什么

两组都运行可写的 workspace-v1 + Packed-v3 Overlay，使用同一份 opt-level=3
二进制。`BREWFS_WORKSPACE_EMPTY_QUERY_CACHE=0/1` 关闭/开启新增空查询缓存。
上层有数据、目录项或删除标记时仍正常查询和合并。

lookup/getattr 缓存、FUSE TTL=0、直接读开关、512 MiB VFS 读缓存、8 MiB
已认证 lower 帧缓存在两组间相同。旧补丁中的帧缓存经单独核对后作为共同底座接入，
其收益不计为本次空查询缓存的收益。没有引入旧补丁中的强制只读或上层绕过开关。

负载一为原来的 13 B/64 B 两文件；负载二包含覆盖写、截断再扩展、删除、新建和
重命名。先读热空查询，再修改，随后逐次核对文件内容与目录。修改发生在计时前，
第二组测的是修改后的读取性能。每单元 128 轮，按 off/on、on/off 交换顺序。

## 复现

需要 Linux/WSL、FUSE、Docker、Python 3.11+、boto3，以及脚本中固定 digest 的
本地 Redis/RustFS 镜像。脚本不拉取镜像；每批核对 RustFS 指定分支与镜像的源码
标签，不一致则停止。当前镜像来自 `feat/dynamic-block-size-rebased` 的 f4ed2025。
Linux 源码文件须为 LF 换行；Windows checkout 可在 WSL 内重新检出。

在 BrewFS checkout 中构建并检查，输出目录必须不存在：

```bash
bash tools/perf/build_hot_overlay.sh /absolute/new-build
bash tools/perf/check_hot_overlay.sh /absolute/new-checks
```

在本目录运行，需要 root 挂载 FUSE。WSL 请直接以 root 启动 shell；若依赖代理，
保留该 shell 的代理环境，避免嵌套 sudo 丢失代理。

```bash
/path/to/python-with-boto3 run_local.py \
  --binary /absolute/new-build/brewfs \
  --producer /absolute/new-build/packed_v3_snapshot_fixture \
  --source-commit "$(cat /absolute/new-build/source-commit.txt)" \
  --source-patch /absolute/new-build/source.patch \
  --out /absolute/new-run --pairs 3 --passes 128
python3 summarize.py /absolute/new-run > /absolute/new-summary.json
python3 verify_results.py /absolute/new-run
```

重算已交付数据，无需挂载或启动服务：

```bash
python3 summarize.py evidence/run-03 evidence/run-04
python3 verify_results.py evidence/run-03 evidence/run-04 \
  evidence/guard-p1-before-r3 evidence/guard-p1-after
python3 -m unittest test_meter
```

混合读写检查使用 `run_compose_guard.py --repository /absolute/brewfs-checkout
--binary /absolute/brewfs --out /absolute/new-guard`，调用仓库现有 Compose `fio-randrw`。
需要脚本中固定的 perf/aws-cli 镜像及干净的专用 checkout。原版和候选分别运行，
再用 BrewFS 的 `tools/perf/compare_artifacts.py` 比较两个产物目录。

## 记录与清理

每个单元保留配置、挂载日志、原生 stats、Redis commandstats、RustFS 代理计数、
逐次延迟、文件摘要和 SHA256SUMS。最后一次预热及正式测量有任何 RustFS 请求，
或计时边界仍有请求未完成，则该单元失败。计时循环不读取统计文件。

Redis 计数只扣除采集器自身的 INFO，包含 Lua 内部命令，不能当作网络 RPC 次数。
总耗时包含 Python 循环与内容校验。退出时只清理本次 owner/随机名称对应的容器、
卷与临时目录；失败日志保留。

私有 workspace 后台同步目前是设计，见 BrewFS 的
`doc/architecture/private-workspace-sync.md`。本次代码仍等待远端元数据确认。
