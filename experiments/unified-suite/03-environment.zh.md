# 环境、构建与维护

> 历史协议，仅用于原冻结计划；新实验见[当前方案](current/README.zh.md)。

所有命令从本仓库根目录运行，执行端 Linux、Python ≥3.11，构建smallfiles需要Rust ≥1.88。控制端状态库放独立本地磁盘；远程节点的 repo/bin/input/result 各自有固定绝对路径。先准备以下变量，随后文档复用：

```bash
export RUN=/srv/control/unified-20261102
export REPO=/opt/ax-experiments
export BIN=/opt/ax/bin
export DATA=/srv/ax-data
export OUT=/srv/ax-results
export BREWFS_SOURCE=/srv/build/brewfs
export RUSTFS_SOURCE=/srv/build/rustfs
export REPLAY_BUILD_DIR=/srv/build/ax-tools
```

以上目录须按实际机器填写；results 不在被测 FUSE mount 或输入树内。不要把模板路径直接当成已有设备或已有授权资源。

## 机器与连接

每个 pool 至少明确 client、handoff target、metadata、objects 的角色及所有物理共享关系。可使用已有 SSH 容器、VM、裸机；executor 可以多个隔离 slot，实际 2 CPU/8 GiB/swap=0，别复用同一个 cgroup 当多个 Agent。服务数量按冻结拓扑配置，三节点 PD/TiKV 是实际三个节点，不能用目录数代替节点数。

单公网跳板，业务节点走私网。SSH 配置使用固定 known_hosts、StrictHostKeyChecking 与 ProxyJump；不关闭 host key 检查。镜像/二进制通过私网分发，每个平台/features/profile 只构建一次。同一 block 不混主机规格、RustFS 版本和部署拓扑。

```bash
python3 -m bench.runtime.fleet template --cluster pool-a \
  --executors pool-a-exec --services pool-a-meta pool-a-objects \
  --hosts pool-a-client-host pool-a-target-host --output "$RUN/fleet-a.draft.json"
# 编辑实际 host_id/environment_id/slots/host_observer_node/data_paths 和归属。
python3 -m bench.runtime.fleet check --inventory "$RUN/fleet-a.json" --output "$RUN/fleet-a.check.json"
python3 -m bench.runtime.fleet probe --inventory "$RUN/fleet-a.json" \
  --node pool-a-exec --slot pool-a-exec --ssh-config "$RUN/ssh.config" \
  --known-hosts "$RUN/known_hosts" --output "$RUN/probes/pool-a-exec"
```

为每个实际执行 slot、服务、宿主观测端分别 probe，保留生成的 peer。`owned_by_campaign=false` 的共享资源不得随实验删除。pool 的 `observer_ids` 必须包含本池全部宿主/客户端/服务观测者；`resource_ids` 包含共享盘和端点，别靠 SSH alias 判断独立性。

按 [baseline→集群映射](12-parallel-baselines.zh.md) 为pool-b及其他池各建独立inventory；BrewFS/JuiceFS各自占有client、metadata与objects服务。若共用TiKV/RustFS或物理宿主，必须登记同一resource ID，此时测量被串行以保留隔离。各池核相同硬件/服务预算及实际校准，保存跨池等价资格。构建产物可共享一份并经私网分发，运行数据、namespace、缓存与observer目录独立。

## 版本与依赖

1. 保留 Git SHA、dirty diff、工具链、构建参数、OS/kernel、FUSE、fio `--version`、JuiceFS版本、TiKV/Redis版本及镜像 digest。确认 `/usr/bin/fio` 是真实 Flexible I/O Tester。
2. 依 [RustFS 指引](../../infra/rustfs/README.md) 同步指定 branch-latest，构建带 OCI revision 的镜像，再机械核对 source/image。服务四个 layout 开关默认 true。
3. BrewFS 按选定路线构建 workspace/native/packed、TiKV 和 hint 所需 features。记录 Cargo features；先核 `mount --help` 和 `workspace --help`，再做真实挂载资格。实验代码不自动 checkout 或切换运行时。
4. 编译本仓库工具，保存产物 hash 与构建日志；分发后逐节点复核。

```bash
CARGO_TARGET_DIR="$REPLAY_BUILD_DIR" cargo build --release --locked --manifest-path tools/fs-probe/Cargo.toml --bin smallfiles
CARGO_TARGET_DIR="$REPLAY_BUILD_DIR" cargo build --release --locked --manifest-path tools/full-fuse-replay/Cargo.toml
python3 -m bench.suite_support manifest --files "$BIN/smallfiles" "$BIN/full-fuse-replay" \
  "$BIN/brewfs" "$BIN/juicefs" /usr/bin/fio "$RUN/backend-version.json" \
  --output "$RUN/build-manifest.json"
```

构建目录中的 release 产物须先由维护者分发到 BIN；上面 manifest 命令只做核对，不复制。manifest 不收私钥/凭据。凭据使用受保护的环境/文件，不进 argv、Git 或公开归档。

## 维护与容量资格

```bash
python3 -m bench.runtime.maintenance --paths "$DATA" "$OUT" \
  --output "$OUT/maintenance-before.json"
```

在所有宿主执行，记录 timer、自动升级进程、reboot-required、boot ID、磁盘/inode 和数据挂载设备。正式运行前安排专属维护窗口，完成待升级/重启，确保测量期无自动重启服务计划。该 helper 只读，不会停止更新服务。共享机器无法保证维护/干扰隔离时，不授予正式性能资格。

data、对象、metadata WAL、producer TMPDIR、原始日志和归档分别核容量；预算包含同时保留的 base、fresh upper、失败现场、全部原始指标、下载中的临时文件。C4 eager copy 按 1M×4 KiB×children 加元数据和余量估算，64 children 的容量不足时必须调整实际 pool 或另列未执行条件，不能偷偷缩数据集。

观测先做成对开/关校准，使用同一 workload、fresh cache和至少三对、≤5%容差；5秒采样，最大 gap30秒。详见 [证据](10-evidence.zh.md)。采样 HTTP 超时可以标失败，不得借此杀实验或回填缺口。
