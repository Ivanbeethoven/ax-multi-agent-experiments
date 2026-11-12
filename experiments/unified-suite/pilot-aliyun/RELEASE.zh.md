# 本轮资源收尾

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

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

A 组原清单位于本机 `/home/hxy/ax-unified-pilot-prep/release-evidence/freeze-02/`，包含 3 台私网实例、9 块附属盘和 1 块新协调盘。[只读查询](evidence/preparation/release-live-check-02.json)确认它们仍在，未生成结束计费事件。执行代码固定在同级 `tools-01/`，原始 API 和代码已另存本机并[逐成员核验](evidence/preparation/release-flat-local-retention.json)。

B 组 [18:03 只读核验](evidence/preparation/release-pool-b-freeze-01/result.json)确认实际仍是原 3 台私网实例和 9 块盘，附属盘都随实例释放。[三份释放清单及 SHA256](evidence/preparation/release-pool-b-freeze-01/release-set-pins.json)位于本机 `/home/hxy/ax-unified-pilot-prep-next-01/release-evidence/pool-b-freeze-01/`。B 组清单排除原跳板、协调盘、A 组、网络和 OSS。两组仍在运行，本次只锁定范围，没有停止、删除或分离任何资源。

最后停止本轮跳板服务，核新协调盘已归档、卸载后再分离并删除；原跳板切回`StopCharging`。原跳板系统盘、EIP、VPC、交换机、安全组和OSS数据保留。费用估算是按报价分段计算，最终账单另核。

## 退役的 On1 专用采样盘

`d-bp16jaiu2fcmbij8mg7i` 是旧 C4 On1 单独创建的 100 GiB 盘。当前 C2 的 A 池启动恢复只需要四块原 RustFS 数据盘，使用 `a-pool-up.py objects --without-legacy-observer-scratch`。原 Flat 完整复制流程已经退役；历史恢复结果见[独立 OSS 读回](evidence/preparation/c4-observer-fold-real-03/recovered-01/publication-01/README.zh.md)。

等所有实验停机后，在新建的本地目录执行：

```powershell
./experiments/unified-suite/pilot-aliyun/scripts/retire-on1-scratch.ps1 -Action Preserve -ReceiptRoot $receipt -SshConfig $sshConfig
./experiments/unified-suite/pilot-aliyun/scripts/retire-on1-scratch.ps1 -Action Release -ReceiptRoot $receipt -InventorySha256 $reviewedInventorySha256
```

第一条只启动原跳板和 A objects，只读清点精确 UUID 的盘，收回原被拒收 tar 和未归档的小文件，核 SHA 后卸载、停机。核对 `inventory.json`、`extra.json`、原始 tar 和卸载回执再执行第二条。只有阿里云查询确认该 ID 已不存在才记为释放；不删除其他数据盘或 OSS。
