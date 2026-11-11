# 本轮资源收尾

先完成结果与全部校准原始文件的归档，独立下载并核全部成员，再停止负载、正常卸载并确认进程退出。`cloud.py release`负责发送释放请求，`release-evidence.py`只查状态，不执行删除。

释放前，按原创建记录和节点配置核清机器的系统盘、全部数据盘及`DeleteWithInstance`。清单不允许多盘、换盘或混入原跳板机：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/release-evidence.py freeze \
  --ledger "$LEDGER" --role "$ROLE" --node-spec "$NODE_SPEC" --out "$FREEZE"
```

`ROLE`为`client`、`metadata`、`objects`或`target`。`NODE_SPEC`使用该节点原始部署配置，`LEDGER`使用本轮原资源账本。只扩容原盘仍可核验，新增或替换磁盘则先补清实际归属。协调数据盘使用`--role coordinator-disk`，不传`--node-spec`；它必须是新建的100GiB数据盘，原跳板和原系统盘保留。

实际释放完成后，用原`release-set.json`和其SHA256，在新目录查询：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/release-evidence.py check \
  --release-set "$FREEZE/release-set.json" --release-set-sha256 "$RELEASE_SET_SHA256" \
  --out "$CHECK"
```

只有实例及原清单内每一块盘都不存在，才生成`cost-event.json`，供`record-cost.py --events`合并计算。仍有资源时返回2，保留实际ID和状态，不生成结束计费事件。停机不收费、删除请求返回成功、实例已消失而盘仍在，均不算完整释放。请求失败或响应不完整也不能生成证明。

本轮清单位于本机`/home/hxy/ax-unified-pilot-prep/release-evidence/freeze-02/`，包含3台私网实例、9块附属盘和1块新协调盘。[只读查询](evidence/preparation/release-live-check-02.json)确认它们仍在，并正确拒绝生成结束计费事件。实际执行代码固定在同级`tools-01/`，原始API和代码已另存本机并[逐成员核验](evidence/preparation/release-flat-local-retention.json)。未来目标机另建清单，结束时再做真正的释放后查询；当前没有删除资源。

最后停止本轮跳板服务，核新协调盘已归档、卸载后再分离并删除；原跳板切回`StopCharging`。原跳板系统盘、EIP、VPC、交换机、安全组和OSS数据保留。费用估算是按报价分段计算，最终账单另核。
