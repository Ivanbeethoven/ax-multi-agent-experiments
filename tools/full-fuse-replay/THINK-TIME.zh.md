# 多 Agent 回放的固定总量随机等待

**默认关闭。** 原命令及 --performance 均不插入等待。只有显式传 --simulate-llm THINK_PLAN.json 才启用；计划必须声明 agent_count≥2。真实模型运行已经包含 LLM 等待，不再叠加模拟。

## 语义

等待放在已核验的工具调用/Agent 轮次边界、对应的下一条 FS 事件之前。不会逐 FS 操作休眠，不改事件顺序、写入字节、句柄生命周期或错误检查；不会用文件系统空闲时间猜 LLM 边界。

指定总预算 T（精确到毫秒）与 K 个边界：每个间隔在 0–15000 ms，计划间隔之和严格为 T。T 超过 K×15000 直接拒绝。生成器从当前可行范围随机分配剩余额度，最后打乱分配；算法与种子入档。因总量约束，各段并非独立均匀采样。只有一个边界或 T=0/15K 时没有可用随机自由度。

同一逻辑 Agent、任务、区组两臂复用同一计划，不把 filesystem/host 名写进 seed 或 agent-id。不同 Agent 使用不同稳定 ID；不同任务/区组使用冻结的种子映射。总预算必须显式填写，没有默认 T。公平性需在 cohort 调度单中保证，两次单独传参不自动保证配对。

## 第一步：边界映射

从真实工具/轮次日志提取并对齐到完整 events.jsonl，保存 agent-boundaries-v1 JSON：

```json
{
  "schema": "agent-boundaries-v1",
  "reviewed": false,
  "events_sha256": "实际完整 events.jsonl 摘要",
  "evidence": [{"path": "agent-events.jsonl", "sha256": "实际日志摘要"}],
  "boundaries": [
    {"before_seq": 42, "kind": "tool", "id": "tool-002", "evidence_index": 0, "evidence_locator": "实际记录位置与对齐依据"},
    {"before_seq": 501, "kind": "turn", "id": "turn-003", "evidence_index": 0, "evidence_locator": "实际记录位置与对齐依据"}
  ]
}
```

示例不具备运行资格。evidence 路径相对边界文件且在同目录树内；逐字节核 SHA。before_seq 严格递增，不能在 init/destroy 或末尾外。审阅时证明时钟关联/工具边界与 seq 的对应，确认无并行工具或后台 I/O 被错误当作全 Agent 思考空档后才置 reviewed=true。脚本验证引用与摘要，不自动证明人工标注的语义。没有可信对齐证据的旧 trace 只做默认无等待回放。

## 第二步：生成一份计划（不执行）

```bash
python3 bench/trace_think_plan.py --events "$EVENTS" --boundaries "$BOUNDARIES" \
  --agent-count 64 --agent-id a001 --seed 20261029 --total-think-seconds 12 \
  --output "$RUN_DIR/a001-think.json"
```

此示例 T=12 s；实际 T 由实验调度单冻结。生成器流式检查整份事件及 SHA、边界证据、容量，保存每段 delay_ms；只能写新文件。保存计划本身的 SHA 后给两臂复用。

## 第三步：AI 核计划后，显式开启一次回放

```bash
full-fuse-replay "$EVENTS" "$BLOBS" "$DEDICATED_ROOT" "$NEW_RESULT" \
  --performance --simulate-llm "$RUN_DIR/a001-think.json"
```

Rust 在任何被测文件变更/休眠之前核整份 trace 的 SHA、序号、总行数、边界和预算。预检读取 input，不计入 execution_seconds；两组都应在计时外完成相同输入核验，输入放被测挂载外且冻结，计时期间不修改。

## 输出与精度

- execution_seconds：回放阶段总墙钟，含插入的休眠；默认关闭时口径保持原样。
- active_replay_seconds：墙钟扣实际休眠，仍包含 JSON/载荷读取、检查和调度开销，不能当纯 I/O 时间。
- think_time：开关、计划 SHA、Agent ID、种子、计划总毫秒、每段计划/请求/实测等待、已执行边界数与调度误差。
- 调度可能让 sleep 超时；之后仅从剩余思考间隔扣回超时量，不扣文件系统耗时。最后一次睡眠的残余超时无法消除，照实报告；保证计划精确求和，不假称 OS 墙钟能绝对精确。
- 回放失败不会为补足 T 再睡眠；报告是部分执行，不能作为完整计划的性能值。异常退出由单步收据保全，不自动重放。

该开关模拟固定预算下的间歇访问，不复现真实模型延迟分布，也不重建原进程调度。已有工具/轮次映射的采集适配仍需逐 trace 验证。
