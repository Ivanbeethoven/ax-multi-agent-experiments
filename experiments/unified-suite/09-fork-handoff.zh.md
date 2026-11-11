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

本批最新版的[本机空版本派生检查](pilot-aliyun/evidence/preparation/c4-native-empty-fork/README.zh.md)已通过：两个child使用同一初始空revision，三个工作区分别写入并冷重挂读回，互不影响。它没有包含百万文件，也没有把父工作区后续写入发布为新revision；完整C4仍需先打通非空base发布，不能用这个检查替代。

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

tag对每child唯一，挑战覆盖overwrite/append/truncate/unlink/rename/new-file及文件/目录fsync。挑战前生成预期最终树：在独立本地副本执行固定修改，或使用先经交叉检查的生成器；保留规则与SHA，不能拿被测输出自身当expected。完整树检查使用 `fs_step check --expected ... --expected-sha256 ...`。检查child远端最终状态、parent和兄弟不变、普通卸载/退出。大树完整遍历的实际时间全部记verification，不隐去慢项。

pilot已提供[`fork-oracle.py`](pilot-aliyun/scripts/fork-oracle.py)：读取提前关闭并核过SHA256的完整输入清单，按固定输入公式算出六项操作后的完整预期树，不读取被测child。两种系统使用相同tag与预期结果；同一组的不同child使用不同tag。输入文件模式为0644、目录0755，执行修改时固定`umask 0022`。完整清单只覆盖名称、类型、模式、内容，文件所有者和链接关系另查。

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/fork-oracle.py \
  --base-tree "$BASE_TREE" --base-tree-sha256 "$BASE_TREE_SHA256" \
  --fixture-proof "$FIXTURE_PROOF" --fixture-proof-sha256 "$FIXTURE_PROOF_SHA256" \
  --tag "$CHILD_TAG" --out "$EXPECTED"
```

命令要求100万文件、每文件4KiB。`$EXPECTED/result.json`记录固定首读文件的内容摘要及完整`expected-tree.json`摘要。对实际child执行`fs_step mutate --tag "$CHILD_TAG"`，冷重挂后以这份清单检查；对parent仍用原输入清单。预期生成的耗时单列为准备，不计入派生时间。

报告创建wall/每child延迟、ready及首读、前台任务、COW与排空、校验、metadata/object增长、实际copy字节。shared lower派生和eager copy均完成相同最终视图，支持有限语义的路径单列。

## M：原生会话 stay / move

沿用 [handoff.py](../../bench/runtime/handoff.py)，它执行原生会话export/import/resume。完整阶段：停止新工具 → checkpoint/export → 源排空/普通卸载/退出 → writer fencing → transfer原生session JSON → 目标挂载同一后端/原workspace → import → resume → 新工具成功 → export-after / 会话身份验证。

```bash
python3 -m bench.runtime.handoff --spec "$HANDOFF_SPEC" --sha256 "$HANDOFF_SHA" --output "$OUT/handoff"
```

stay和move从同一预注册checkpoint与任务suffix开始；stay也支付相同quiesce/export/stop/restore步骤，目标留在原executor；move换到实际另一宿主。N>1时其余邻居持续执行冻结负载，保存窗口吞吐/慢化；无邻居证据就没有“流转不影响其他Agent”的结论。

共享repo和XDG由文件系统承载，目标使用空HOME。恢复测试显式选择 `restore_method=shared-workspace-db`：目标打开相同工作区内的原数据库/XDG路径，不另传或导入session JSON，不复制workspace数据树。`native-export-import` 保留为另列的兼容性测试。保留source/target host ID、原生session ID、工具call ID、lease/generation、原writer释放和target新writer证据。actual checkpoint hash先固定，再构造restore spec；不能在测量中改写expected SHA以便通过。

流转测首个新增完成工具事件、恢复到任务完成、停顿和上传债务；export/import命令耗时不是完整恢复时间。fresh-only entity不能进入现有namespace重开的主表，可在功能完成后新协议加测。

另报 `state_ready_seconds`：从源端停止准备开始，到目标重新挂载并核对原会话历史结束，后续模型调用不计入这一项。`first_completed_tool_ack_seconds` 保留完整流程耗时，包含后续模型调用。两项分别报告，不能把模型等待时间算成文件系统的开销。
