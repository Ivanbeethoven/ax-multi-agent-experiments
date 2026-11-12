# 资源清理与核验

原 A/B 两池已各释放 3 台实例和 9 块盘。jump、40 GiB 系统盘、100 GiB 协调盘保留；最新小试结束后，jump 已恢复 StopCharging。未删除其他任务的资源。

- [A 池最终回执](evidence/preparation/a-pool-retirement-01/README.zh.md)
- [B 池最终回执](evidence/preparation/b-pool-retirement-01/README.zh.md)
- [最近小试的机器、临时密钥和规则清理](evidence/preparation/rbm-five-checks-20261112/README.zh.md#版本复现与清理)

## 后续使用方法

先结束负载、排空、正常卸载并确认原进程退出。将不可再生的结果和失败证据放到盘外独立核验；保存范围按用户要求，不自动下载整套 trace 或二进制。

按**创建回执中的具体 ID**核对资源归属，不能按 `ax-pilot-*` 名称批量删除。没有本任务创建证明的资源不处理。恢复实验先建立新账本，旧 A/B 清单只供查档。

以下脚本仅查询并生成证明，不发送删除请求。先固定实例和全部附属盘的范围：

```bash
P=experiments/unified-suite/pilot-aliyun/scripts
python3 "$P/release-evidence.py" freeze \
  --ledger "$LEDGER" --role "$ROLE" --node-spec "$NODE_SPEC" --out "$FREEZE"
```

`ROLE` 为 `client`、`metadata`、`objects` 或 `target`。`LEDGER` 必须是实际创建账本，`NODE_SPEC` 必须对应当前节点。新增、替换磁盘要先补齐归属证明；本轮保留的 jump 和协调盘不属于释放范围。

按核实的 ID 释放后，在新目录查询：

```bash
python3 "$P/release-evidence.py" check \
  --release-set "$FREEZE/release-set.json" --release-set-sha256 "$RELEASE_SET_SHA256" \
  --out "$CHECK"
```

只有实例及清单内全部磁盘均不存在才生成 `cost-event.json`，供 `record-cost.py --events`使用。删除请求成功、实例消失而盘仍在、StopCharging，都不等于完整释放。停机仍可能有磁盘费用。

原机器专用的备份、释放和 On1 采样盘脚本已经退役，可从 [历史清单](../../../archive/README.md)找回；不要对新资源直接运行旧脚本。
