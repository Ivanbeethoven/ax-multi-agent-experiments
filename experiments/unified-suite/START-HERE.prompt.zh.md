# 初始任务 prompt

你负责维护本仓库统一C1–C4实验的基本环境、构建产物和单步脚本。先读AGENTS.md和experiments/unified-suite/README.md，再读01-method、02-ablations、03-environment、04-inputs、05-execution、10-evidence、11-ai-handoff、12-parallel-baselines，以及本批实际plan、inventory、资源账本和state status。

实验包含普通fio、完整AI trace重放、1M×4KiB随机小文件、Agent fork与原生会话流转。外部baseline仅JuiceFS，BrewFS native及D/P/G为内部对照和消融；纯消融资格不满足就明确blocked，不借系统路径差异解释单技术收益。

架构必须支持不同baseline在不同集群并行。使用baseline-parallel及显式product_pools/arm_pools：默认BrewFS→pool-a、JuiceFS→pool-b，可单独将native/packed/各消融臂放到更多实际池。逐cell领取与关闭，配对block只按同负载/参数/轮次汇总结果；较快的池可独立推进下一条件。实际共享host/disk/service使用相同resource ID，冲突cell不得同时测量。跨池比较核实相同硬件/服务预算、输入、版本与校准；保留固定集群分配的限制。

先盘点构建、依赖和CI的实际状态。已有手工构建/manifest流程不等于已完成自动流水线；缺少编译→本地检查→打包→私网分发→节点hash核验时，先补齐可审读的分步脚本和验证，报告实际完成范围。每批按AGENTS同步指定RustFS分支最新源码再冻结SHA/digest；同源码/平台/features只构建一次，所有池复用一致产物。单公网跳板，其余走私网；固定host key，不在worker上重复编译。

在已授权范围内维护环境；需要购机或新增资源时先给出具体inventory、容量与费用方案，再按用户授权执行。正式运行前按实际机器填完整摘要、维护窗口、observer、slots与各baseline原生路线资格，生成新plan和drafts/queues.json。只在一个控制端独立本地盘维护状态库，各执行者通过同库claim本池cell，再调用既有runtime单步远程执行；不复制DB或开新DB绕锁。

准备/导入按实际pool/dataset/representation/layout一次记账，同池及共享资源不与测量混跑。性能段保留逐读字节验证；完整base扫描在不可变asset首次完成一次，child验证touched/父兄弟隔离/远端持久化。默认同步、fsync、强排空、普通卸载、真实退出与原始观测不省略。

每步保留exact输入SHA、started/PID/boot/start tick、原始结果与退出。未知不重放，started不回pending；恢复只接管原操作未完成部分并标failed/recovered。归档核外SHA和内部逐成员清单；证据完整且真实关闭后释放本cell调度占用，再按已授权资源账本核归属并及时释放自有资源。报告全部准备、读写、校验、失败、中断等待、恢复、归档及创建至释放费用，只使用完整无中断且验收通过的配对比值。

环境变化、故障、完成或需要用户决定时通知；无实质变化保持安静。每次交接更新CURRENT.json中的各池active_leases/active_stages及下一单步动作，不改变冻结状态。
