# C4 校准不通过时，人工交接 B 组

仅用于六轮原校准都已正常关闭、5% 开销门槛拒绝、正式 C4 从未启动的情况。C4 保持未验收，原 watcher 和成功回执路径不变。工具只读协调节点已有证据，不连接执行节点，也不启动 C2。

协调节点上，待 `ax-pilot-b-c4-jfs-resume-01` 退出后，从包含此脚本的代码目录运行：

```bash
P=/srv/ax-pilot-control
python3 experiments/unified-suite/pilot-aliyun/scripts/audit-c4-overhead-release.py \
  --request "$P/preparation/c4-resume-overlay-01/resume.request.reviewed.json" \
  --request-sha256 5ae8c6ee525f0fdeb9915b8df47ca9ca2ef70089c318e339cb5dc9846ddc51c2 \
  --unit ax-pilot-b-c4-jfs-resume-01 \
  --invocation 14b74049f70a470ea21fd3a4e045c4d0 \
  --c2-watch-request "$P/preparation/c2-handoff-watcher-01/watch.request.json" \
  --c2-watch-sha256 fd5d3f66dbf141ca180b6fc0eac678ab228bcee45564fa1bf585310396f5ea93
```

默认只打印审计。需要留存时，显式加 `--out "$P/fork-control-01/handoff-b-c4-overhead-to-c2-01"`。该目录只能新建，里面的 `physical-release.json` 证明资源边界，`result.json` 供人工交接；两者都标明 `c4_accepted: false`。`handoff-b-c4-to-c2-01/result.json` 只允许正式 C4 成功时由原 watcher 写入。

审计逐项绑定六轮、十二次原生挂载退出、归档核验、真实 5% 拒绝、协调进程退出、原物理锁和无残留进程／挂载／lease。最后一轮对象节点的采样只证明 collector 当时存活；C2 的原锁内检查仍需确认当前 collector、空 slot、空数据库和产物版本。

人工切换前先确认正式交接 watcher 已停止等待并保留失败记录，原 C2 watcher 没有写 launch intent，C2 单元和原输出均不存在。由 C2 owner 结束原等待 watcher 后，以其请求中的同一 runner、unit、recipe 和输出路径启动一次，唯一替换的是 `--handoff` 路径及 SHA。不要运行两套 watcher，也不要用备用回执改写 C4 状态或补造正式样本。
