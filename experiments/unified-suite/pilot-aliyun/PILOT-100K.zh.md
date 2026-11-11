# 10万文件试跑

2026-11-11按用户要求，C3和C4派生先使用100,000个4KiB文件，每份409,600,000B（390.625MiB，不含目录和元数据）。C1、完整C2 trace、C4原生会话恢复的设置保持原值。系统路径仍为28项，消融另计；本轮结果标为10万文件试跑。

| 项目 | 本次设置 |
|---|---|
| C3 | 1个Agent、1个工作区、8线程、1024文件热集；冷热读各8192次、COW 256文件、随机写256次、混合操作1024次，每次写均fsync |
| C4派生 | 独立固定输入，1个child；完整修改、父子隔离和冷读回检查 |
| 文件大小 | 全部4KiB，完整检查100,000个文件，不抽样验收 |
| 环境 | 先复用现有四块60GiB对象盘；首份实际base导入后重新核空间、inode和Redis余量 |

C3八阶段合计20,224次读、768次写，应用读82,837,504B、写3,145,728B；这些不等于对象和物理磁盘的读写量，三层指标分别收集。512MiB读取缓存保持原设置，10万输入可全部容纳，因此本轮不能回答超出缓存容量时的表现。正式单元从新挂载、空缓存开始；本机资格检查先做完整读回，会预热缓存，其耗时不能当作正式数据。

输入生成使用已有脚本的显式文件数参数。以下两条顺序执行；数据进入`$BUILD/inputs/pilot-100k/`，记录进入`$BUILD/receipts/pilot-100k/`，输出路径须未使用：

```bash
sudo bash experiments/unified-suite/pilot-aliyun/scripts/prepare-inputs.sh c3 "$BUILD" 100000
sudo bash experiments/unified-suite/pilot-aliyun/scripts/prepare-inputs.sh c4 "$BUILD" 100000
```

C3目录名为`smallfiles-source`，C4为`fork-source`，两者生成规则不同。`smallfiles-oracle.py`和`fork-oracle.py`也必须显式传`--files 100000`，并使用对应10万文件输入的原始核验记录。导入、控制者准入和回放工具从冻结plan读取文件数，百万文件证明不能作为10万输入的证明。

生成新阶段草稿：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/configure-pilot.py \
  --phase c3 --files 100000 --campaign ax-pilot-100k-c3 --out "$NEW_C3_DRAFT"
python3 experiments/unified-suite/pilot-aliyun/scripts/configure-pilot.py \
  --phase fork --files 100000 --campaign ax-pilot-100k-fork --out "$NEW_FORK_DRAFT"
```

两份草稿分别有4项和5项，尚未绑定实际输入/环境，不能直接执行。正式冻结仍须补齐现有inventory、构建和10万输入清单；当前C1/C2单元及校准结束并归档后才切换阶段，旧状态库和百万文件计划保留。

若在云上重新生成输入，`prepare-cloud-inputs.py --files 100000 --sources-only`只准备C3/C4；在新目录执行，省去重复下载和回放C2。`close-cloud-inputs.py --files 100000 --sources-only`还要求显式指定实际`--preparation-unit`及本机C4完整清单摘要。该新增云端入口尚待实际执行验证。

10万规模能减少准备和容量开销；entity原生lower的32,768行限制、BrewFS工作区重挂后写入EIO仍需分别处理，不能因规模降低就认定对应路径已通过。原[256GiB扩盘备选](OBJECT-CAPACITY.zh.md)暂不执行，实际容量不足时再决定。

本机准备已通过：C3完整核验100,000个文件、409,600,000B，独立Rust工具另核全部名称、内容、权限、归属和链接；C4生成100,100项完整目录清单，并提前计算六类修改后的完整预期状态。首次并行准备遇到脚本快照创建冲突，原失败保留；C3在新记录中单独执行成功。快照发布已改为完整临时文件加原子链接，两进程同时准备的检查通过。详细摘要及原始包位置见[准备记录](evidence/preparation/pilot-100k/README.zh.md)。这些是输入准备结果，尚无10万文件云上性能结果。
