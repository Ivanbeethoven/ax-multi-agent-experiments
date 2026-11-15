# C2 完整 trace：首次云端预检

2026-11-12，A 池，运行 `c2-current-a-20261112t090606z`。磁盘、Redis 和 RustFS 预检通过，控制脚本在 `stage-client` 失败。`worker_launched=false`，未回放 1,747,109 次回调，未做冷挂校验，没有性能样本。

| 检查 | 实际结果 |
|---|---|
| [容量](capacity.json) | A 池四个对象盘、元数据盘、客户端盘和协调者均满足本次资格运行的字节与 inode 预留线。最小对象盘可用 28,069,122,048 B。 |
| [Redis](redis.json) | 7.0.15，AOF 已开启，`appendfsync=always`，`noeviction`，未在重写 AOF。 |
| [RustFS](rustfs.json) | 运行镜像和版本匹配；四个动态布局开关均开启。[版本回执](latest.json)记录了运行前的 BrewFS 和 RustFS 分支校验。 |
| [客户端准备](controller-failure.json) | 实际运行的控制脚本与 `b77344f` 中的文件一致。准备 cgroup 的命令调用 `shlex.join()` 时把两个 `pathlib.Path` 对象直接放入参数列表，引发 `TypeError`；worker 尚未启动。[停止前回执](ready-for-stop.json)的清理检查未执行，不能把 `false` 当作发现残留挂载。 |

[协调者关闭回执](closed.json)标记 `passed=false`、`formal_sample=false`，并固定 A 池停机回执的 SHA-256。[实例状态](instance-stop-checks.json)记录 A 池三台与跳板停机。17:09:59（北京时间），[独立 API 复核](independent-closure.json)确认七台实例均为 `Stopped / StopCharging`，本次未新建实例或磁盘。回执未包含 trace 或环境变量文件。

后续修复后用新运行编号做完整回放、冷挂和内容校验；本次只记预检证据，旧套件正式验收仍为 14/28。
