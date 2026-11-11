# C4执行与观测

已实现下述入口，小文件回归通过；尚未执行云端C4，也未把功能预演计作正式样本。只支持JuiceFS `clone -p`和BrewFS `flat-v1`完整拷贝；其余臂直接拒绝。

```bash
python3 "$FROZEN_REPO/experiments/unified-suite/pilot-aliyun/scripts/run-c4-batch.py" \
  --request "$REQUEST" --sha256 "$REQUEST_SHA256"
```

请求为`c4-fork-batch-v1`、`reviewed=true`。所有文件引用均为真实绝对路径和SHA256，不含凭据。

| 字段 | 必须绑定的内容 |
|---|---|
| `control`、`cell_id`、`initialize_state` | 新C4 control和明确cell；首次建库须显式`true`。该目录须已备好冻结plan、provenance、fleet、allocation、probes和SSH配置 |
| `runtime_control` | 可选的实际新runtime绑定证明；改变repo路径须重新探测和资格，不能修改旧证据的SHA |
| `preparation_request` | `run-c4-fork-baseline.py`请求引用，worker明确`mode=prepare`；本池全新asset、空DB、源输入及关闭回执；canonical仍为原`parallel-control-01` |
| `asset`、`qualification`、`out`、`formal_attempt` | 全新本池资产、资格、batch输出及正式child名字；不能复用C3资产/旧attempt |
| `tools` | `baseline,capacity,prepare,calibrate,check,admit,run`七个入口的冻结引用 |
| `fork_tool`、`node_tool`、`observer_tool_name`、`observer_tool_sha256` | client实际部署的C4 leaf、pilot-node与三角色observer builder |
| `shared_qualification`、`hardware`、`cross_pool_qualification` | 本池已核的指标定义；匹配实际fleet的新硬件资格；匹配本C4 plan的双池等价证明 |
| `objects_container`、`collector_name`、`collector_out` | 本池当时真实RustFS容器及collector原始名字/回执，不能填写已停止旧实例 |
| `trials` | 预登记6项`off,on,on,off,off,on`；每项含`mode,attempt,out,collector_name,collector_out`，重开collector另给`new_collector_name,new_collector_out`；名字须沿实际前项结果连续 |

`run-c4-batch.py`始终持有原`parallel-control-01/runner-locks`的本池pool/实例/盘锁，检查canonical、显式其它旧control及新C4 state的本池或资源相交lease。另一池可继续；handoff复用另一台client才需要锁双方。

执行顺序：真实容量before→一次完整base导入/fsync/零债务/正常卸载→新缓存完整base校验/正常卸载→容量after与7份额外完整副本、20%余量检查→6个fresh child校准→原开销检查→admit→一个正式child。`prepare-c4-controller.py`收集原始证明和独立期望树。所有child名字不同，修改内容固定`child-01`，名字不进入写入字节。

每个trial由`run-c4.py`执行原suite阶段；`c4-fork-node.py`是真实worker。三角色observer每5秒采样、最大间断30秒，保留原`/proc`、cgroup、Redis、Prometheus、物理盘和网络数据。client绑定实际main及cold-verify两个mount state；不制造第三个copy mount。`measure-start`确认后才cohort go；真实worker结束后`measure-end`；两次原生零债务后`drain-end`；全量父子冷验、正常卸载后`verify-end`。

主指标`create_to_first_read_seconds`来自worker单个连续单调时钟，起点在clone/copy开始前，终点在首个4096B文件内容核对成功后，包含中间查找与首次使用等待。另保留命令、首读、完整child fsync、固定修改/fsync、排空及全量冷验耗时；逻辑应用字节与observer采集的物理I/O分列。全量冷验检查父目录不变、child符合事前独立oracle。

三对校准按`(1,2),(4,3),(5,6)`比较cohort makespan与任务P99，门限≤5%；N=1的任务P99只是单次任务时间。原生FUSE计数/直方图保留，但不会编造缺失P99。off停止外部observer/collector，原生指标与RustFS OTLP发送开关不变；重新启动collector使用新名字和新目录。

正式归档只列实际observer、两个mount及supervisor、worker、leaf和已记录的clone/copy进程；核原PID/boot/start、正常退出、slot空、挂载消失、两次原生零债务。逐成员SHA校验、原始observer审计、`suite_validate`和`sample`验收全通过后才关闭原suite state。失败保留started/lease与现场，不重试、不kill；若原observer/worker/mount仍在或启动身份未知，协调者继续持canonical锁，仅重复只读退出核验。

部署须冻结新代码目录，不能覆盖正在运行的code12/13或已冻结code14。旧draft-02只是code14功能预演请求，不能直接用于本formal入口。

本轮coordinator已建立`/srv/ax-pilot-control/fork-control-01`，仅准备小文件，尚无state。具体请求草稿在`/srv/ax-pilot-control/preparation/c4-setup-01/drafts-01/`，待B原C3完整关闭、原始归档交接后执行。code15位于`/srv/ax-pilot-control/code-15`，manifest另存`preparation/code-15/manifest.json`；不要假定代码目录里存在`.code-manifest.json`。

[setup-c4-control.py](scripts/setup-c4-control.py)的`control/runtime/hardware`动作分别保留原证据、只更新空闲本池runtime、派生代码路径变化后的硬件资格；指定`--runtime-repo /srv/ax-pilot-control/code-15`可独立分发使用。原硬件/API采样时间不改，另记派生时间和源证明；未更新的另一池不重新探测。`runtime`要求原C3关闭及C2数据准备证明，持原canonical锁，并检查`c123-control-01`、`c3-stage-fixed-01`等实际旧state。

[draft-c4-requests.py](scripts/draft-c4-requests.py)从实际code manifest生成6校准+1正式的具体请求。草稿始终`reviewed=false`；先取得实际runtime、硬件及当前collector证明，再新建reviewed请求，保留原稿。coordinator准备原始回执见`evidence/preparation/c4-setup-coordinator-01/`。两项新helper的5个小回归通过；准备不计C4实验完成。
