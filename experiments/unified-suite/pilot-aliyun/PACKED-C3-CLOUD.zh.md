# Packed C3下一批入口

节点动作：`scripts/packed-c3-node.py`。不编译新工具，不修改当前冻结批次。现有`prepare-packed.py`先发布完整100k输入并检查base，之后每轮只新建私有entity upper，共用同一packed manifest。

先跑云上资格检查，结果不能算正式性能样本：

```bash
python3 "$TOOLS/packed-c3-node.py" qualify \
  --base-asset "$BASE" --asset "$PRIVATE" --plan "$PLAN" --cell "$CELL" \
  --manifest /srv/ax-client/assets/provenance/build-manifest.json \
  --probe /srv/ax-client/assets/bin/entity_metadata_probe --probe-sha256 "$PROBE_SHA" \
  --smallfiles /srv/ax-client/assets/bin/smallfiles --smallfiles-sha256 "$SMALLFILES_SHA" \
  --node-tool "$NODE_TOOL" --node-tool-sha256 "$NODE_SHA" --mount-state "$MOUNT_STATE" \
  --reader "$READER" --reader-sha256 "$READER_SHA" \
  --verifier /srv/ax-client/preparation/pool-a-next-01/smallfiles-verify --verifier-sha256 "$VERIFIER_SHA" \
  --oracle "$ORACLE" --oracle-sha256 "$ORACLE_SHA" \
  --source /srv/ax-client/inputs/pilot-100k-01/smallfiles-source \
  --source-proof /srv/ax-client/inputs/pilot-100k-01/receipts/c3-verify/result.json \
  --private-object-env /srv/ax-client/private/rustfs.env --out "$OUT"
```

实际参数：`BASE`为本次`prepare-packed.py`生成的asset目录；`PRIVATE`、`MOUNT_STATE`、`OUT`均必须新建；`PLAN/CELL`必须属于同一冻结计划的packed C3；probe与smallfiles的SHA必须匹配该计划的build manifest。reader必须有与本次BrewFS构建一致的原链接记录；reader、完整verifier、oracle、node tool均传实际路径和SHA。私密env保持0600且在证据目录之外。启动前该池必须空闲，槽位为2 CPU、8 GiB、swap 0。

`qualify`依次执行fresh、原监督挂载、原smallfiles八阶段、原CSV全量检查、两次native status、正常停、原namespace只读冷验及父目录隔离检查。失败不重跑；保留原记录。

| 正式stage | 接线 |
|---|---|
| create-w01 | `packed-c3-node.py fresh`：保留manifest/bucket/端点，新volume、namespace、catalog，不复制base |
| mount-w01 | 原`pilot-node.py draft-mount/start/worker`；worker传`--root-at-mount --workspace 1`，只挂一次 |
| measure-w01 | 原`workloads.worker`、`cohort.run`、`suite_validate.work`；固定八阶段和全部8个worker CSV |
| drain-w01 | 新`debt`：已构建probe单进程，两次同namespace status相隔≥1秒；保存stdin/stdout、PID/boot/start_ticks、manifest/SHA；扁平债务值取原`debt.bytes/objects` |
| unmount-w01 | 原监督器正常stop；保留原PID退出、mount absent、anchor |
| verify-w01 | 新`cold-verify`：传原stop文件及SHA、原expected-touched.tsv；只读reader验证原child和原parent；不重挂entity |

三角色采集继续用原observer；client builder必须传`--entity`。五秒采样、三对on/off、5%门禁和四个w01阶段mark保持原协议。entity债务来自native probe，不能填flat writeback gauge。producer发布时间、private create、mount、八阶段时间、drain、正常停、冷验分别记录。

`stage-spec --request ACTION.json --out STEP.json`可为fresh/debt/cold-verify生成原`experiment-step-v1`草稿。ACTION为`{"schema":"packed-c3-node-action-v1","action":"debt","arguments":{"asset":"实际路径","plan":"实际路径","cell":"原cell ID","manifest":"实际路径","probe":"实际路径","probe_sha256":"实际SHA","out":"新输出目录"}}`。再用原suite review/run-stage及同一`state.sqlite`和lease执行，不另建状态库。

正式controller尚需接线：entity版三对校准、Packed资格收集/admit、单mount的三角色归档和sample。现有flat run-one/calibrate/archive不能直接用：它们会复制base、重新FUSE挂载并要求三份mount回执。归档必须检查真实单mount及全部reader/probe/observer退出、两份原生零债务、完整raw CSV、三角色raw指标，再调用原archive_check、suite_validate、suite_state.finish；不得补造flat回执。上述接线和云上资格通过前，不能标记正式可运行。
