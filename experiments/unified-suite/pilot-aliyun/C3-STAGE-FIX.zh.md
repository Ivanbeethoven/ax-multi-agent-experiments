# C3 新执行批次

旧批次的 `run-c1.py` 已能识别 `-w01`，但命令入口没有接受该参数。B 的三台采集器启动后，`create-w01` 在参数解析时退出2；没有创建挂载或工作负载。旧记录保留为失败，不计性能样本。

- 旧状态：`/srv/ax-pilot-control/parallel-control-01/state.sqlite`。
- 原6次校准：旧目录的 `calibration/cal-pool-b-j-sf-*`。修复汇总器后重检通过，makespan和task p99偏差约1.594%；没有重跑校准。
- 失败封存：`failed-before-work/pool-b-c3-parser-01.close.json`；三台采集器正常停下，原失败状态经原 `finish` 关闭。
- 新批次：`/srv/ax-pilot-control/c3-stage-fixed-01`。新建空状态库，保留原C3整个case的4个arm；只有JuiceFS本次被准入，其余仍待测。
- 新执行器：`/srv/ax-pilot-control/code-flat-stage-01`。147个文件来自冻结code13，只改命令入口的stage校验；`stage-cli.diff`和`stage-cli-check.json`保存差异及9个真实入口的小检查。

`scripts/rebind-flat-attempt.py prepare`创建新批次。新旧完整cell必须相同；输入、二进制、配置、种子和采样方式不变。新asset只换执行plan、挂载点和目录，`prepared_asset/prepared_plan`保留原准备事实。metric/workload合同保持原字节，因此沿用原6次校准。新库不复制任何旧完成状态；`runner-locks`明确指向原批次的物理锁目录。

`scripts/rebind-flat-attempt.py formal`只执行新准入和本次正式流程，不重复导入或校准。实际请求及所有命令保存在新批次；不修改原失败日志。

`scripts/retain-rebound-calibrations.py`在新正式流程完整关闭后封存原6次校准。原路径、准备plan、校准时间和原raw保留，新正式样本单独记录。归档在云端完成，不下载到本机。

跨批次调度需要检查旧库、新库和公共物理锁；单看旧库没有lease不足以证明B空闲。
