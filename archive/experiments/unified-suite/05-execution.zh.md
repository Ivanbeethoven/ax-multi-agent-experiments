# 冻结、领取与单步执行

> 历史协议，仅用于原冻结计划；新实验见[当前方案](current/README.zh.md)。

## 1. 冻结实际配置

在 [入口](README.md) 生成 config，填入本批 `build_manifest_sha256`、`input_manifest_sha256`、各 pool 的 `inventory_sha256`。摘要由下列命令读取，不能填占位字符：

新模板默认baseline-parallel；先按 [映射说明](12-parallel-baselines.zh.md) 填product_pools及可选arm_pools。计划中每个cell有实际pool与resource_ids，block列全部cell_pools。drafts同时输出queues.json供各池执行者查看。映射改变须新plan/新状态库，不能改已领取的cell。

```bash
python3 -m bench.suite_support pin --file "$RUN/build-manifest.json"
python3 -m bench.suite_support pin --file "$RUN/input-manifest.json"
python3 -m bench.suite_support pin --file "$RUN/fleet-a.json"
python3 bench/scale_plan.py --suite-config "$RUN/config.json" --output "$RUN/plan.json"
python3 -m bench.suite_support drafts --plan "$RUN/plan.json" --output "$RUN/drafts"
python3 -m bench.suite init --plan "$RUN/plan.json" --db "$RUN/state.sqlite"
```

规划阶段可以保留 null 摘要；运行领取会拒绝。修改条件后用新 plan/新状态库，原状态保留。状态库是协调者本地文件，不能放 NFS、FUSE 被测卷或分别复制到多台协调者；其锁没有自动过期，不会因 AI 掉线自动重放。

## 2. 准备挂载与工作单

每个节点先填入本批已核实的原生 JSON 配置（BrewFS也可读取JSON配置），证据/缓存/挂载目录分离。基线参数见 [样例](examples/README.md)。先生成 mount command 草稿，再人工或执行 AI 审读 exact argv、路径、凭据来源和 backend。

新计划的client_profile固定[显式缓存设置](13-defaults.zh.md)。`drafts/<cell>/client-settings.json`列本cell期望值；将实际endpoint/namespace配置与该设置合并到新文件，核对后计算CONFIG_SHA。生成器保留实际连接字段、不启动服务，挂载前仍逐项核资格。

```bash
python3 -m bench.suite_mount --plan "$RUN/plan.json" --cell "$CELL" \
  --config "$NATIVE_CONFIG" --output "$CONFIG"
python3 -m bench.suite_support pin --file "$CONFIG"

python3 -m bench.runtime.prepare mount --filesystem brewfs --cluster pool-a \
  --suite-plan "$RUN/plan.json" --suite-cell "$CELL" \
  --metadata-backend tikv --config "$CONFIG" --config-sha256 "$CONFIG_SHA" \
  --binary "$BIN/brewfs" --binary-sha256 "$BREWFS_SHA" --mount-point "$MOUNT" \
  --cwd "$REPO" --output "$OUT/mount-spec-draft" --metrics-address 127.0.0.1:8910 \
  --fstypes fuse.brewfs --inherit-environment AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
```

实际 FUSE类型、端口、credentials环境名按native资格记录填，不照搬。JuiceFS用自己的binary/config/filesystem。`--metadata-backend` 将资源池和C1–C4旧硬编码解耦。每个workspace分配唯一volume/workspace/namespace，BrewFS缓存目录必须新且空；JuiceFS的memory cache在新前台进程中私有，不创建名为memory的目录。hint消融须把`--dynamic-block`设为cell.arm.hint。prepare和node都重核plan绑定与缓存设置；不能手改argv绕过。旧无client_profile的计划保留原证据与版本，不自动升级到新设置。

审读后调用 review，输出新文件，不改旧草稿：

```bash
python3 -m bench.suite_support review --spec "$OUT/mount-spec-draft/command.json" \
  --reviewer ai-pool-a --note "已核实际后端、私有路径、同步语义、缓存与二进制版本" --output "$OUT/mount-command.json"
python3 -m bench.suite_support pin --file "$OUT/mount-command.json"
```

把新command引用更新到mount草稿，再review mount；worker引用review后的mount；review worker的step后更新worker引用；最后review cohort。顺序为 **叶子命令 → mount/worker → cohort → 外层stage**，每次使用新SHA。review命令只记录审读，不替代运行资格。

## 3. 资格与准备

完成环境、完整输入、真实native路线、缓存/同步实际值、观测开销以及各case额外条件，填写 `qualification.draft.json`。每个gate引用独立可核对的原始结果和typed `equals`，不能全指向一个手写 `passed=true`。未实现的纯消融保持未qualified，禁止换成另一种系统对照后仍用原名称。

```bash
python3 -m bench.suite qualify --db "$RUN/state.sqlite" --receipt "$RUN/qualified-cell.json"
python3 -m bench.suite asset --db "$RUN/state.sqlite" --receipt "$RUN/asset-verified.json"
```

baseline-parallel只要求本cell资格及本池asset齐备；领取时重核嵌套证据hash。多池配对另核cross-pool-hardware-equivalence，资格应引用本block全部实际pool的库存、配置和校准结果。另一baseline自己的native资格/导入未完成不阻止已准备好的池推进。准备期的写入由协调者控制，不能与本池或实际共享资源的测量混跑。旧paired-pool模式仍要求整组资格/资产齐备。

## 4. 领取一个单元

```bash
python3 -m bench.suite claim --db "$RUN/state.sqlite" --pool pool-a --owner ai-pool-a --output "$RUN/lease-a-001.json"
python3 -m bench.suite claim --db "$RUN/state.sqlite" --pool pool-b --owner ai-pool-b --output "$RUN/lease-b-001.json"
python3 -m bench.suite status --db "$RUN/state.sqlite"
```

两条claim都在同一控制机操作同一个本地DB；随后各自远程运行本池stage，可同时持有同一配对block的不同cell。不要把DB拷贝到不同集群独立领取，也不要放共享盘。`cell:null` 表示本池没有满足条件的cell，读原因，不自动减少规模。`already_claimed:true`是原身份，先检查原stage记录，不能开始第二次。一个pool只有一个活跃cell，另一owner不能接管；共享resource ID阻止冲突cell。finish核本cell证据/真实关闭后即释放本池，另一baseline的原锁仍保留。block只在全部arms关闭后标done；done不代表性能验收成功。

## 5. 一个stage一次命令

一般顺序：observe-start → create → mount → measure → drain → unmount → verify → observe-stop → archive。观测先覆盖创建/挂载，尚未存在的client target按阶段声明，不能强求未启动client已有指标。

C3逐workspace展开成 `create-w01` / `mount-w01` / `measure-w01` / `drain-w01` / `unmount-w01` / `verify-w01`，再w02…，前后各一次observe-start/stop和archive；pilot只有w01。**按cell.stages执行**，不把四个生命周期塞进一个不可追踪的loop。C4 handoff的measure是有原始子阶段回执的原生流转程序，随后drain/unmount关闭目标端。

`drafts/<cell>/stages.draft.json` 给出外层stage模版。填一条reviewed `experiment-step-v1` exact argv；测量命令通常为 `python3 -m bench.runtime.cohort --spec ... --sha256 ... --output ...`，其他阶段调用已有fleet/native验证器。外层输入必须pin完整cohort/模块及绑定证据，命令使用绝对Python路径和固定cwd。

```bash
python3 -m bench.suite run-stage --db "$RUN/state.sqlite" --lease "$RUN/lease-a-001.json" \
  --stage measure --spec "$RUN/measure-step.json" --sha256 "$MEASURE_STEP_SHA" --output "$RUN/cell-001/measure"
```

外层state先写intent再启动；同一stage只能登记一次。命令结束才更新complete/failed，没有工作负载超时、自动kill或重试。节点cohort等待全体ready后释放一次屏障；预检失败不会释放；ready确认失败只保留并诊断，不把存活进程当成已停止。

stage可以调用一个审读过的固定子阶段清单（例如C4原生会话流转），不得包入重导入、自动清理、循环重试或动态更改条件。所有子步骤各自保留started/PID/finished。如果无法清晰拆为冻结stage，先在独立协议中说明，不把未记账阶段藏进measure。C3上面的run-stage示例须使用measure-w01等真实stage名。

## 6. 中断与关闭

状态库中的started永不自动变回pending。先核原PID/boot/start tick、远端mount、writer租约、原始日志，判明“已完成/仍运行/未知”。恢复只能接管原操作的未执行部分，必须单独写恢复计划及证据；原cell保留失败或恢复标记，不算完整配对性能。

使用 [证据收尾](10-evidence.zh.md) 封存，生成实际closure和cell-close回执，最后：

```bash
python3 -m bench.suite finish --db "$RUN/state.sqlite" --lease "$RUN/lease-a-001.json" --receipt "$RUN/cell-close.json"
```

finish释放的是调度占用，**不会删除VM/盘/桶**。失败样本同样可以在证据保全和真实关闭后释放调度占用，性能报告仍为失败；不必伪造valid=true才能收尾。
