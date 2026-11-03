# 观测、核验、报告与释放

## 原始证据与观测

每cell保留计划/输入/构建/环境摘要，所有started/PID/真实exit、native stdout/stderr、raw fio JSON或每线程CSV、full trace结果、每workspace/revision身份、缓存统计、元数据/对象/宿主原始样本、归档hash/成员清单、失败/恢复文件。旧停掉的importer没有指标不等于新client故障。

runtime observer用5秒间隔，覆盖executor容器、宿主、metadata与对象服务。字段名、计数单调性、每层字节含义、端点、对象前缀写在metric contract。CPU/RSS、盘吞吐/IOPS/队列、网络、元数据RPC、GET/Range/PUT/bytes、pending uploads、client cache/lower/upper命中各有来源。压缩传输字节不能当应用逻辑字节。

原始采样不回填。升级引起重启时保留维护journal和真实gap，恢复正常不消除原gap。原始阶段不设总时限kill。观测开销资格通过后，才运行相同版本/间隔/密度的正式负载。

至少标记 `measure-start`、`measure-end`、`drain-end`、`verify-end`。跨主机monotonic不能相减，采用各自区间/UTC及coordinator ACK并记录时钟误差。audit重核每条sample的hash与连续性：

C3对每个workspace分别加`-w01`、`-w02`等后缀，实际必需列表以cell.observation_marks为准。同一observer持续覆盖四个生命周期；每个workspace target配置唯一ID与自己的mount_directory，未启动/正常退出由原生回执识别，不能把短命client PID放进全程必须存活的processes列表。原生client的cgroup和metrics target仍需采集。

每个边界向所有observer显式发送mark，收到实际sample回执后保留；cohort原有的`measurement-start`/`application-finished`仍保留，不能冒充C3的四组标记。下例在每个observer对应peer执行一次，其余边界替换label和新的output：

```bash
python3 -m bench.runtime.fleet call --peer "$OBSERVER_PEER" --sha256 "$PEER_SHA" \
  --operation observe-mark --state "$REMOTE_OBSERVER_DIR" --label measure-start-w01 \
  --output "$OUT/marks/measure-start-w01/observer-a"
```

```bash
python3 -m bench.runtime.observe audit --output "$OBSERVER_DIR" --max-gap-seconds 30 \
  --marks measure-start measure-end drain-end verify-end
```

## 工作量与正确性

将每worker native结果下载到控制端，填自动生成的`work-packet.draft.json`：smallfiles列全部CSV，fio列raw fio JSON，trace列原始metrics，fork列burst，handoff列各moved session结果。离线核对不重放负载：

```bash
python3 -m bench.suite_validate --plan "$RUN/plan.json" --cell "$CELL_ID" \
  --packet "$RUN/work-packet.json" --output "$RUN/work-check.json"
```

这只核native工作量/字节覆盖。另需严格内容、父兄弟隔离、实际远端读回、债务归零、原进程退出、缓存策略和观测开销。route没有持久化验证器时，work-check不能代替验收。

填 `sample.draft.json`：8类check引用实际结果，metrics用 `{evidence,field,unit,direction}` 从原始JSON取数；field为点分隔对象路径。数组指标先由保留源码的离线聚合器输出新JSON，不手抄平均p99。`timings_seconds` 列创建、预热、性能、排空、卸载、校验、中断等待、归档等真实段。

原stage全部正常且没有接管时才写uninterrupted=true/recovered=false。被接管的第三轮写recovered=true；phase性能可单列，不能补成第三个完整性能配对。check的成功字段必须是明确布尔值；纯D/P/G的额外条件仍由qualification负责。

## 保全与真实关闭

先下载全部归档到新路径；核外SHA、逐成员路径/大小/SHA、内部manifest、raw操作总数、失败/维护文件和无deferred。下载一半、只有tar目录清单、或只留summary都不够。

本协议用archive_check生成含逐文件SHA的内部manifest，再在下载端独立verify；allowlist.json是审读过的相对文件路径数组。只有已停止写入的证据可以pack，归档输出放root外；不提取tar、不改源文件，失败文件保留。

```bash
python3 -m bench.archive_check pack --root "$CLOSED_EVIDENCE" --allowlist "$ALLOWLIST" \
  --archive "$OUT/client.tar.gz" --cell "$CELL_ID" --kind client --output "$OUT/client-archive-check.json"
# 下载所有角色归档之后，在控制端核远端记录的SHA。
python3 -m bench.archive_check verify --archive "$RUN/cell-001/archives/client.tar.gz" \
  --sha256 "$REMOTE_ARCHIVE_SHA" --output "$RUN/client-download-check.json"
```

`experiment_step seal`封存明确allowlist。执行者把全部应保留文件加入，避免凭据混入；大树用独立list生成器先审读，再传入/调用seal，不能省掉raw。

```bash
python3 bench/experiment_step.py seal --root "$RUN/cell-001" \
  --files work-check.json sample.json archives/client.tar.gz archives/objects.tar.gz archives/metadata.tar.gz \
  --output "$RUN/cell-001.retention.json"
python3 bench/experiment_step.py verify --root "$RUN/cell-001" \
  --manifest "$RUN/cell-001.retention.json" --manifest-sha256 "$RETENTION_SHA" \
  --output "$RUN/cell-001.retention-check.json"
```

上面allowlist仅示意，实际包括全部stage/native/失败证据（可在完整归档内），保留内部manifest核验结果。实际Linux节点填写 [closure request](examples/closure.request.json)，列全部原进程身份、mount点、至少两个间隔≥1秒的原生零债务观测和target identity：

```bash
python3 -m bench.suite_support closure --request "$CLOSURE_REQUEST" --output "$OUT/closure.json"
```

helper机械检查PID+boot+start tick、mountinfo和原生计数。不得漏子进程/其他mount namespace；每节点执行，控制端合并时保留所有节点证据并要求全部true。真实退出码0和未用force/lazy unmount另核原mount finished。债务零不等于已退出。

`suite-cell-close-v1`含plan_sha256/cell_id/outcome、closure的processes/mounts/debt引用与equals、retention.root及manifest引用、全部archives引用、sample引用。finish会重新核全部归档外SHA/内部成员；执行者按冻结inventory核齐所有角色，样例只列一个不能直接当完整。failed必须带failure_reason；complete要求原stage全完成。调度锁释放与性能验收分离，参考 [close样例](examples/cell-close.json)。

## 费用与报告

costs.json记录每个资源唯一id、created_at、deleted_at（尚未删除则as_of_utc）、hourly_rate和currency。network/账单调整通过bills原始证据及amount_field列出，报价估算不是已核账单。计费粒度、预留容量、共享服务分摊写scope。

```bash
python3 -m bench.suite_report --db "$RUN/state.sqlite" --costs "$RUN/costs.json" --output "$RUN/report.json"
```

报告重核证据、raw观测及完整block；未完成、失败、恢复samples都有行，只有完整accepted block产生对比。性能比值>1表示该指标相对JuiceFS更好，不跨指标/成本/完整校验泛化。消融列全部arms原值与限制，按预声明方向解释，不择优删慢条件。

## 释放

证据完整校验和真实进程/挂载/债务关闭后，按已有授权及时显式释放账本自有VM、保留数据盘、独占SG，逐项核owner/tags/id并查询不存在，不等报告/PR/CI。共享VPC/vSwitch/keypair/服务保留。当前工具不调用provider删除API；维护者使用审读过的provider步骤并保留返回与后查。

观测失败但证据完整、诊断已保留时明确failed收尾，不用valid=true绕过成功cleanup门槛。归档不完整/现场未知时保留资源诊断并计费。结束清除有限本机保活/采集进程，确认无遗留keepawake/定时负载。
