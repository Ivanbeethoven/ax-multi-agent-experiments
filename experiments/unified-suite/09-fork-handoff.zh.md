# C4：fork 与跨机流转

## F：固定 revision 的批量派生

使用独立 `fork-fixture-million-4k` asset：1M×4KiB，目录命名遵循既有fs_step，内容固定。它与C3输入算法不同，独立记录准备成本。

```bash
python3 bench/fs_step.py fixture --root "$DATA/fork-source" --files 1000000 --file-bytes 4096 --output "$OUT/fork-fixture.json"
python3 bench/fs_step.py snapshot --root "$DATA/fork-source" --output "$OUT/fork-source-tree.json"
```

先静止/排空源并取得不可变revision，源和所有child身份预先记录。比较路径：

| 路径 | 原生操作 |
|---|---|
| flat native | 物化完整独立拷贝；若用cp则固定 `--reflink=never -a`，不用硬链接树冒充copy |
| native-workspace | 本批 `workspace ... fork REVISION --count 1 --data-config ...`，记录实际返回workspace ID |
| entity native / packed | 从同一冻结manifest建立fresh namespace；包括首次绑定/挂载就绪，保留fresh-only限制 |
| JuiceFS | `juicefs clone -p BASE NEW_CHILD`，等原生命令真正完成 |

workspace目录接口与entity目录接口不同，不能把workspace-v1控制命令套到entity namespace。通过本批 `--help` / native smoke 固定exact argv。每个child一个命令；重复请求是否幂等须由原生语义证明，工具不会自动retry。

`fork_burst.py` 接收固定children清单（[样例](examples/fork-burst.json)）。所有step提前review并pin，在新输出目录保存整个intent；有界线程同时提交原命令，等待全部真实退出，不因一个child失败杀其他child。`parallelism` 主条件等于children，若资源不足，单列其他排队条件，不能悄悄降低并发。

```bash
python3 -m bench.fork_burst --spec "$BURST_SPEC" --sha256 "$BURST_SHA" --output "$OUT/fork-burst"
```

结果仅声明创建命令完成，`performance_accepted=false`。随后各child挂载、首读/任务初始化及隔离验证都必做。第一个可用的计时点是读取指定文件内容成功，不是CLI返回202或列表里出现名字。

```bash
python3 bench/fs_step.py first-read --root "$CHILD" --file d000000/f00000000 \
  --sha256 "$SOURCE_FIRST_FILE_SHA" --output "$OUT/first-read.json"
python3 bench/fs_step.py mutate --root "$CHILD" --tag "$CHILD_ID" --output "$OUT/mutation.json"
```

tag对每child唯一，挑战覆盖overwrite/append/truncate/unlink/rename/new-file及文件/目录fsync。挑战前先在独立本地副本生成预期最终树，保留规则与SHA；不能拿被测输出自身当expected。完整树检查使用 `fs_step check --expected ... --expected-sha256 ...`；利用immutable base+delta的独立验证器也需先资格通过。检查child远端最终状态、parent和兄弟不变、普通卸载/退出。大树完整遍历的实际时间全部记verification，不隐去慢项。

报告创建wall/每child延迟、ready及首读、前台任务、COW与排空、校验、metadata/object增长、实际copy字节。shared lower派生和eager copy均完成相同最终视图，支持有限语义的路径单列。

## M：原生会话 stay / move

沿用 [handoff.py](../../bench/runtime/handoff.py)，它执行原生会话export/import/resume。完整阶段：停止新工具 → checkpoint/export → 源排空/普通卸载/退出 → writer fencing → transfer原生session JSON → 目标挂载同一后端/原workspace → import → resume → 新工具成功 → export-after / 会话身份验证。

```bash
python3 -m bench.runtime.handoff --spec "$HANDOFF_SPEC" --sha256 "$HANDOFF_SHA" --output "$OUT/handoff"
```

stay和move从同一预注册checkpoint与任务suffix开始；stay也支付相同quiesce/export/stop/restore步骤，目标留在原executor；move换到实际另一宿主。N>1时其余邻居持续执行冻结负载，保存窗口吞吐/慢化；无邻居证据就没有“流转不影响其他Agent”的结论。

共享repo/HOME/XDG由文件系统承载，跨机只传原生session JSON，不复制workspace数据树。保留source/target host ID、原生session ID、工具call ID、lease/generation、原writer释放和target新writer证据。actual checkpoint hash先固定，再构造restore spec；不能在测量中改写expected SHA以便通过。

流转测首个新增完成工具事件、恢复到任务完成、停顿和上传债务；export/import命令耗时不是完整恢复时间。fresh-only entity不能进入现有namespace重开的主表，可在功能完成后新协议加测。
