# C3 Packed：开机前生成资格草稿

以下命令只读仓库里保留的原始回执，向本机新路径写 JSON。不连接 ECS、协调者、Redis 或 RustFS，不启动任何任务。

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/draft-c3-packed-fresh-qualification.py \
  --attempt c3-reboot-20261112t120000z --out /tmp/c3-reboot-20261112t120000z.json
```

草稿锁定原 100k 计划、输入清单和验证结果、云资源清单、BrewFS/RustFS 构建、`code-12` 的 producer、原挂载工具和修过 Redis ACL 的 `code-packed-node-01` 资格工具。它给出三组命令参数：两池运行探测、发布新 parent、检验新 child；worker 和协调者各有路径及 SHA 清单。使用实际本轮时间生成新的 attempt。JSON 有 `reviewed:false`、`execution_enabled:false`，Redis 库号留空等人复核；不要将草稿当作可执行批准件。

1. **开机前先解除 B 池阻塞。** 原 `setup-controller.py` 会探测两池；对象节点的工作根目录至少要有 10 GiB 可用。B 盘 0 [上次实测](evidence/preparation/b-c1-post-gc-capacity-01/statvfs.json)为 4,040,151,040 B，差 6,697,267,200 B。A 池冷查结果不能代替 B。两条最新分支查询命令在 `branch_lookups`；如果版本前进，不把新二进制换进旧计划。
2. **在确认的窗口补齐实际运行绑定。** 取原物理锁并核所有控制库无冲突；按 [重启后的原生命令链](C1-B-STORAGE-STEP.zh.md#重启后的原生命令链) 新建控制，复制草稿里 `suite_plan/provenance` 的确切原字节，复用 canonical 锁目录，重新取得本次 API、挂载、六台 runtime probe 和跨池硬件证据。核对 worker 各路径的实际字节及 SHA，不用历史 boot、PID 或 allocation。旧 `setup_controller` 和远端资格工具的路径在草稿中有原始来源，当前远端文件仍须核验。
3. **同次启动采样并跑资格。** 先确认 A 当前容量、六个输出路径全新、槽为空、Redis 库号未占用（`DBSIZE=0`），再按 [容量窗口](C3-PACKED-CAPACITY-WINDOW.zh.md) 采三份 before。只有复核后才替换 parent 参数里的 `<REVIEWED_EMPTY_REDIS_DATABASE>`，依次调用 `parent_argv`、`child_argv`，再采 after。原单次 producer 和完整基线验证就用了约 40 分钟，不包括 child 资格、冷读和校准。任一步失败保留原件，停止派发。

新 parent/child 必须进入当前 `collect`、容量和六轮校准合同；旧通过回执只用来定位工具和输入。草稿不会生成通过标记或领取正式单元。
