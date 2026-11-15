# B 组已停机，数据盘保留

2026-11-11 21:18，三台 B 组实例均经 ECS API 确认 `Stopped / StopCharging`，9 块原云盘仍在原实例上。没有删除、解绑、扩盘或重跑负载。A 组及原跳板机继续运行。

| B 组节点 | 停机确认时间（北京时间） | 保留磁盘费 / 小时 |
|---|---|---:|
| client | 21:18:04 | ¥0.252 |
| metadata | 21:18:22 | ¥0.168 |
| objects | 21:18:46 | ¥0.546 |

B 组从 ¥4.566/小时降至 **¥0.966/小时**，节省 ¥3.60/小时。连同仍运行的 A 组和本轮协调盘，合计 **¥5.637/小时**。21:21:31 新增资源累计估算 **¥126.33**。按各节点实际确认时间分段计费，保留盘继续计费；不含原跳板机、网络、OSS 和账单取整。见 [费用合计](cost-total.json)、[逐盘实时报价](quote/quote.json)、[停机原回执](stop/result.json)。

实验完成数仍为 **14/28**。B 组 C1 的 Off1、On1 完成，On2 失败，后续三轮和正式测量未开始。失败记录不改写；原文件留在九块盘上，待归档路径在 `stop/result.json` 的 `raw_archive_pending`。C2 的正式三角色包及完整六轮校准包已在协调者核验，四包仍待上传 OSS，具体路径也在该文件中。

A 组 C4 首轮继续执行。21:20:34 原复制进程尚在，累计逻辑写入 286,474,240 B，目标文件内容共 409,600,000 B；尚未完成复制和完整冷读验证。见 [原进程记录](a-worker-liveness-02.json)。后续轮次仍先等待观测数据容量处理。

## 可复用工具

- `scripts/stop-idle-pool.py`：默认预检；显式 `--apply` 才对清单中的三台实例执行正常停机。核对原失败、零债务、卸载、进程退出、资源锁和磁盘保留，14 项测试通过。失败批次与成功批次分别验证，不要求把失败批次重跑成功才能停计算费。
- `scripts/build-stopcharging-cost-events.py`：读取已保存的停机与报价 API 原文，生成费率变更；不访问云端。2 项测试通过。后续成本记录必须给 `record-cost.py` 传入 `--events /home/hxy/ax-unified-pilot-prep-next-01/receipts/b-stop-cost-events-01/events.json`，否则会继续按旧计算费率估算。
- 原失败收尾与请求生成源见 [收尾记录](../b-c1-on2-failure-recovery-01/README.zh.md)。停机请求为 `/srv/ax-pilot-control/preparation/b-c1-failed-stop-request-02/request.json`，SHA256 `936b208de29de5d84913c815b28a1660840c31445c5a17fd8897d8dcfc0276f6`；执行工具部署在独立 `stop-code-02`，SHA256 `c40f4718a7f24674242a7203f0766c7b1154a9c4d50f6d05edfd0e5fe9a9e77e`。

原 API 回执完整目录在本机 WSL `/home/hxy/ax-unified-pilot-prep-next-01/receipts/stop-b-failed-apply-01/`。本目录保留停机请求、确认状态、保留盘报价及费用事件的原字节；来源和 SHA256 见 `source-files.json` 和 `cost-source-files.json`。旧账本与旧费用记录未改。以后唤醒 B 组，须重新核验启动身份、服务、槽位和挂载，不能沿用停机前的运行资格。
