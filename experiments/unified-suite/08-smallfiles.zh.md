# C3：百万随机小文件与热读写

使用 [smallfiles Rust程序](../../tools/fs-probe/src/bin/smallfiles.rs)。它固定每文件4096字节，按file/version/workspace确定内容；每次读核全部4096字节，每次写同步fsync。8线程按hot set分片，不互相写同一个文件；所有arm使用相同seed和workspace序号。准备/全量验证见 [输入](04-inputs.zh.md)。

main每cell四个新工作区依次执行，均从同一经校验base派生，各自生命周期包含创建/挂载/测量/排空/退出/验证。创建的成本保留，不用旧写过的upper“省”COW。热集两个独立条件：1024文件（逻辑4MiB）和16384文件（逻辑64MiB）。

| 顺序 | 阶段 | main每工作区操作量 | 检查 |
|---|---|---:|---|
| 1 | cold-read | 65,536 reads，范围全1M | 新client，随机源内容 |
| 2 | lower-prewarm | 热集完整读两遍 | 预热耗时/字节单列 |
| 3 | hot-read | 65,536 reads，固定hot set | payload/帧/对象命中证据 |
| 4 | cow | 256个不同文件首次写 | unique workspace内容、fsync |
| 5 | upper-prewarm | 256 upper文件读两遍 | 验首次COW结果 |
| 6 | hot-write | 2,048次随机覆盖 | 每次递增version、fsync |
| 7 | upper-rewarm | 同256 upper文件读两遍 | 验最新upper内容 |
| 8 | mixed | 8,192次，精确6,144读+2,048写 | 上层已热，3:1、逐读核字节 |

1024热集下每workspace共140,288次已验读、4,352次写；这里**包含预热**。16384热集的读数由生成器计算，别复用1024的总数。每phase单独保留JSON/每线程CSV，报告主读/写数字时明确分母，不能把预热次数混成额外随机读吞吐。

```bash
python3 -m bench.runtime.workloads --request "$SMALL_REQUEST" --output "$OUT/small-worker-draft"
```

[request样例](examples/smallfiles.request.json)中的params从冻结cell复制，seed取plan.config.seed。工作量packet每项列从0开始的agent_index、workspace_index；request的workspace固定为 `1 + agent_index * workspaces + workspace_index`，同一配对内对应child一致。验证器核原生结果中的seed、workspace、各阶段参数和逐线程原始操作数，不能复制一个workspace结果充数。节点worker绑定真实监督mount；Rust程序本身不启停mount、不会创建VM。

本机开发可直接调用 `smallfiles run` 做小输入测试；正式单元按cohort/阶段控制运行。结果 `remote_durability_verified=false` 是实情：前台fsync尚未证明下游债务归零和独立远端读回。

## 校验移到正确的位置

每个asset完整遍历/读内容一次并记成本；每child性能段逐读验证、退出后验证本child的全部touched文件，再检查父base及其他child没有被它污染。生成的 `expected-touched.tsv` 列出实际最终version：

```bash
"$BIN/smallfiles" verify-touched --root "$CHILD_READBACK" --output "$OUT/child-readback" \
  --state "$OUT/workload/expected-touched.tsv"
"$BIN/smallfiles" verify-touched --root "$BASE_READBACK" --output "$OUT/base-isolation" \
  --state "$OUT/workload/expected-touched.tsv" --expect-base 1
```

另一个已写child用它**自己的**expected-touched验证，未写兄弟用expect-base；不能把已写兄弟当成全原始内容。检查新upper命名/对象/元数据绑定和没有额外文件，命名空间异常保留为失败。此workload不创造/删除文件，namespace应与已验证base一致。

支持原生重开的路径做停止所有client、空缓存冷重挂再读回；fresh-only entity不能用创建另一个namespace替代恢复原upper，应使用本批已资格通过的远端logical/object验证器核同一namespace、manifest和upper。该验证器缺失则remote_readback gate不能通过，热读写结果只能保留为未验收操作记录。

## 热性能解释

分别报告cold lower、hot lower、first COW、hot overwrite、upper mixed。两次顺序预热并不保证帧缓存都命中；要用实际resident/miss/eviction、GET字节和页缓存模式解释。若BrewFS热读更快而COW或同步写更慢，都在同一张阶段表保留；一个热set条件不能推广为全流程胜出。
