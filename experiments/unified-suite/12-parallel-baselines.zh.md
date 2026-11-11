# 不同 baseline 在不同集群并行

> 历史协议，仅用于原冻结计划；新实验见[当前方案](current/README.zh.md)。

执行结构是一个控制端、多个独立测量集群、每池一条cell队列。配对block只标识相同负载/参数/轮次的结果集合；执行占用属于cell。每个集群独立推进，下表只是三池放置示例，实际规模由inventory与授权确定。

| pool | 执行方法 | 运行方式 |
|---|---|---|
| pool-a | BrewFS packed及未单列的BrewFS方法 | 按本池队列逐cell执行 |
| pool-b | JuiceFS，包括stay/move条件 | 与pool-a/pool-c并行 |
| pool-c | BrewFS native | 与其他独立池并行 |

配置后按[路径入口目录](baselines/README.md)交付各池任务：JuiceFS、BrewFS四条系统路径及消融各有独立文档。同池多条路径仍由一个owner按队列执行；入口文件不替代实际pool/inventory/resource隔离。

## 1. 显式映射

`suite template`的新systems模板默认BrewFS→pool-a、JuiceFS→pool-b。要拆出native，在config添加实际pool-c（完整字段同其他clusters项，须换成自己的host/disk/service/observer IDs及inventory摘要），并配置：

```json
{
  "placement": {
    "mode": "baseline-parallel",
    "product_pools": {
      "brewfs": ["pool-a"],
      "juicefs": ["pool-b"]
    },
    "arm_pools": {
      "native": ["pool-c"],
      "packed": ["pool-a"]
    }
  }
}
```

这是config中的placement片段，不是完整可执行配置。arm_pools按case.arms的精确名称匹配，优先于product_pools；例如`native-workspace`、`juicefs-move`、`cache-on`、`D0P1`都是独立arm名。未单列的arm按product映射；未分配、池名不存在、重复池ID、未知arm或指定池均无足够slots/不支持suite时拒绝plan，不静默改池。

每个case实际可选池必须有相同hardware_class；该字段只是规划检查，正式资格还需实际机器/服务/校准证据。各池先冻结同一源码、构建、输入、seed、metadata backend与同步语义。不同机器的性能必须保留实际硬件来源。

消融模板以精确arm映射把每个条件的臂交替放到pool-a/pool-b；多于两个臂时同池臂依次运行。有更多独立集群可逐臂覆盖，例如`cache-off`→pool-c、`cache-on`→pool-d；纯消融的同引擎/其他因素一致资格仍须通过。改放置不意味着未实现的D/P组合已经可测。

## 2. 各池独立领取与结束

在同一个协调者主机的两个终端/执行者操作同一份本地状态库：

```bash
# A和B可以领取相同paired_block的不同cell。
python3 -m bench.suite claim --db "$RUN/state.sqlite" --pool pool-a --owner ai-a --output "$RUN/lease-a-001.json"
python3 -m bench.suite claim --db "$RUN/state.sqlite" --pool pool-b --owner ai-b --output "$RUN/lease-b-001.json"
python3 -m bench.suite status --db "$RUN/state.sqlite"
```

claim只检查本cell资格、对应pool/asset的实际准备以及资源冲突。每池最多一个活跃cell，owner/token绑定该cell；同owner重复claim返回原lease，不启动第二次。未知状态、started或失联不自动过期。两个调用的数据库事务短暂串行，远端stage可并行持续运行。

执行者从`drafts/queues.json`找到本池cell/asset列表，再按各cell.stages远程执行，遵循[单步文档](05-execution.zh.md)。queues是预览，不授予执行资格。结果按cell ID下载至控制端，归档路径和命名空间独立。

本cell的全部证据保全且原进程/挂载/上传债务关闭后，finish释放本cell实际资源。A可继续下一条件，即使B仍在前一条件；A不能释放B的锁。失败同样须真实关闭和保全才释放，报告保留失败。block在全部arms关闭后标done，性能报告仍逐样本验收。

## 3. 独立集群与共享资源

每池拥有独立client、metadata、objects及数据盘时，可以真正同时测量。只换SSH别名、命名空间或bucket，底层仍共用TiKV/RustFS、宿主CPU或盘，则各pool登记相同resource ID；claim将冲突cell串行。不能删除共享ID来制造并行。

导入/准备由协调者按实际资源安排；库外手动导入不受测量锁自动管理，禁止与占用同一资源的测量混跑。各pool分别记录一次实际asset导入和校验；共享构建产物只编译一份并经内网分发，逐节点核hash。

## 4. 轮换是显式选项

固定baseline→集群是默认设计。若有两组等价、均支持两条路径的独立集群，希望在操作重复之间交换放置，可配置：

```json
{
  "placement": {
    "mode": "baseline-parallel",
    "product_pools": {
      "brewfs": ["pool-a", "pool-b"],
      "juicefs": ["pool-b", "pool-a"]
    },
    "arm_pools": {}
  }
}
```

列表经suite/slots过滤后按repeat从0开始取模选择。上例r1/r3为BF-a/JF-b，r2为BF-b/JF-a。同池在不同轮次仍只有一个活跃cell。若两个方法列表顺序相同，同轮会落到同池并串行；计划的cell_pools和queues展示真实放置，不保证任意映射都同时执行。轮换到新池要求该池实际asset准备完毕，并独立计入一次导入/校验；不会自动复制已挂载的base。

## 5. 跨池汇总与历史兼容

跨池资格`cross-pool-hardware-equivalence`引用本配对涉及的实际inventory、CPU/内存/盘/网络/服务拓扑与校准证据。同池资格不替代该证据。sample保留实际pool，report按冻结block的condition与轮次汇总：完整、无中断、全部验收通过才生成整组比值。完成先后不改变配对；缺失、恢复、慢阶段、失败和准备成本持续保留。

固定方法与集群绑定仍可能混入集群效应，report的comparison_scope明确保留这一限制。实际没有换机就不能写成换机实验。不同pool并行wall不相加充当campaign wall；费用按唯一资源从创建到释放计算。

历史无placement的config/plan保留原同池整组锁和顺序，摘要及已开始状态不改。新配置如确需旧行为，设置`"placement":{"mode":"paired-pool"}`且不附映射。正在运行的批次不得迁移模式；用新campaign/config/plan/DB，保留原证据。
