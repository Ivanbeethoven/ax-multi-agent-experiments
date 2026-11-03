# BrewFS 消融执行入口

负责独立`--study ablations`的精确条件，不从systems cell临时改参数。先读[共同执行步骤](README.md)和[技术点定义](../02-ablations.zh.md)。协调者使用独立campaign/config/plan/DB冻结本研究；同一消融campaign的各池仍共用一个控制端本地DB。

## 条件、arm与路径入口

| case / 技术点 | 精确arm | 路径差异与资格 |
|---|---|---|
| `delta` / D | `delta-on`、`delta-eager` | [native-workspace](brewfs-native-workspace.zh.md)；同引擎共享lower vs eager私有物化，记录完整复制成本 |
| `packed-format-native-control` / 格式路径 | `entity-native`、`packed-cache-off` | [entity-native](brewfs-entity-native.zh.md) vs [packed](brewfs-packed.zh.md)，双方cache=0；保留payload布局差异 |
| `packed-metadata` / P | `packed-on`、`packed-off` | 同entity引擎、cache=0、相同payload对象/帧布局，只改metadata表示 |
| `payload-cache` / 缓存 | `cache-on`、`cache-off` | 同packed格式/对象，512MiB vs 0；核实际cache计数 |
| `client-object-size` / G之g | `g-262144`、`g-1048576`、`g-4194304` | [native](brewfs-native.zh.md)，固定有效B/q等其他因素 |
| `backend-layout` / G之B | `B-off`、`B-65536`、`B-262144`、`B-1048576`、`B-4194304` | native，g=4MiB，核同enabled RustFS上的实际对象布局 |
| `checksum-quantum` / G之q | `block-only`、`block-and-quantum` | native，固定实际B=1MiB，核distinct q和服务切换资格 |
| `delta-packed-interaction` / D×P | `D0P0`、`D0P1`、`D1P0`、`D1P1` | 同entity引擎、cache=0，四格同payload/同步语义及真实物化能力 |

此表对应[方法目录](../../../bench/suite_catalog.py)，不是开关已实现的证明。缺同引擎eager物化、native lower寻址、相同payload布局或四格资格时保持blocked并留缺项；不能换一种系统路径后继续称纯技术点收益。

## 分配到集群

ablations模板按精确arm交替分到pool-a/pool-b；多于两臂时同池顺序运行。需要更多并行时用[arm_pools](../12-parallel-baselines.zh.md)逐臂指定独立集群，例如cache-on和cache-off分别给两池。所有arm的product均为brewfs，仅改product_pools不能区分消融臂；已有arm_pools覆盖优先，须同步修改它。

映射只用于实际存在于本研究cases中的arm。不能把systems映射的`packed`复制给消融计划：本计划使用`packed-on`、`packed-cache-off`等精确名字。实际服务级布局开关不同的臂须隔离服务；共用RustFS endpoint时保留同resource ID并顺序测量，不能边跑另一臂边切服务设置。

## 准备、运行与报告

从对应路径入口核本cell.arm的实际配置覆盖。cache-only条件同池可复用同一不可变asset，不同pool各有实际导入/校验账本；layout不同必须独立资产。资格、原生工作量及原始布局/缓存计数分别留证，不通过手写passed补适配器能力。

各池依[共同执行步骤](README.md)领取原cell并运行其stages，结果按同case/轮次汇总全部arm。未实现组合、慢阶段、失败与恢复单列；比较按预声明因素及指标解释。g、frame、B、q不合并成一个“块大小”，系统路径收益与纯消融收益分别报告。
