# 历史容器实验：BrewFS 对比 JuiceFS

> 历史参考：本目录保留输入目录、下载/观测工具和早期验证。当前 `final_plan.py` 与 `trace_oss_audit.py` 仍读取 [archives.json](archives.json)；下文预算、campaign 和基线安排不用于新一轮。当前入口是[四集群方案](../../experiments/retest-2026-10-29/PLAN.zh.md)；旧五 RQ 参数清单已[归档](../../archive/README.md)。

2026-10-24。**当前唯一外部对照是 JuiceFS CE＋Redis＋RustFS。**先跑通 JuiceFS，再加入 BrewFS 和自身消融。本轮不部署 AX 控制器，使用已有文件系统 trace；不新增模型调用。

2026-10-26：[当前执行指令](execute-agent.prompt.md)采用4节点RustFS、GitHub最新BrewFS与JuiceFS对比。[负载目录已补齐为7份](data-catalog.md)，包含OSS路径、公网访问条件、大小/SHA256、下载与回放命令。此前按4份生成的任务计划仍保留原输入。

按此顺序执行：**[部署服务](deployment.md) → [下载数据并回放](workload.md) → [核对指标与导出](observability.md)**。下面的配置适用于只能进入已分配容器、不能创建 Pod/Service 的环境。

## 容器清单

| 名称 | 首轮数量、每容器资源 | 运行内容 | 可写目录建议 |
|---|---|---|---|
| w0、w1 | 2 × 2 CPU / 4 GiB | JuiceFS FUSE客户端、Rust回放、资源采集、日志发送 | 输入80 GiB；工作区和缓存另留40 GiB；结果5 GiB |
| redis | 1 × 2 CPU / 4 GiB | Redis 7.2.16、redis_exporter、资源采集 | 持久目录20 GiB |
| s0 | 1 × 4 CPU / 8 GiB | RustFS 1.0.3、资源采集 | 持久目录200 GiB |
| monitor | 1 × 2 CPU / 4 GiB | Prometheus、OTel Collector、结果接收进程、资源采集 | 持久目录50 GiB |

首轮共 **5 个容器，只验收功能和采集**。正式客户端规模为 **4、16、64、128个，每容器4个并发工作区，最高512个并发**；另测1千、1万、10万个已保存工作区，以及256个活跃工作区中的部分迁移。资源、任务数量和停止条件见[正式实验规模](scale.md)。磁盘为起步申请量，先按实际解压和并发工作区占用检查余量。

客户端需要实际可用的 `/dev/fuse`、挂载许可和trace中chown操作所需权限。Redis/RustFS/monitor需要平台已提供的持久目录。所有地址填入 [hosts.example.json](deploy/hosts.example.json) 的副本；容器间须允许部署表列出的端口。容器数和物理节点数分别记，节点未知就填unknown。

## 实验清单

| RQ | 输入与操作 | JuiceFS如何做 | 主要结果 | BrewFS消融 |
|---|---|---|---|---|
| RQ1 工作区容量 | 原F1 1 GiB；N=16/64/256/1024，读4 MiB、改1 MiB、保存64 KiB状态；120秒验收，持有到180秒 | 对已导入父目录使用原生clone，每工作区独立目录；整个批次保持挂载 | 按时正确数/N、排队/创建时间、客户端与元数据内存、后端请求 | 共享/不共享 × Packed/普通元数据 |
| RQ2 分叉与保存 | 父目录分16个子目录；各改1/16/128/512 MiB，连续/分散分别测；历史先1000版本 | 原生clone；写完关闭/同步文件，等待远端保存条件满足，再在另一客户端核验 | 分叉/保存/恢复时间、实际上传字节、保存前后后端占用 | 私有增量/完整物化 |
| RQ2 共享产物 | 原F2 1 GiB；1/16/64消费者；同读/分散/全读；V2仅改2622条记录 | 发布后禁止写入该版本目录；消费者绑定明确版本路径，不给每人复制数据 | 生产到最后正确结果、发布时间、GET/PUT量、返回字节 | 固定/选择B,q × 范围/整对象读取 |
| RQ3 文件负载开销 | 四份trace；首轮每份一次共4项，C=1/4；主组每份16份共64项，C=1/4/16/64 | 同一JuiceFS挂载下使用独立工作区，操作与校验模式不变 | 正确数、批次时间、各题回放时间、CPU秒、内存、FUSE与S3请求 | 完整系统/普通元数据/完整物化/固定布局 |
| RQ4 状态交接 | 16工作区，迁0/4/8/12，其余继续读写；C=1/4；迁移/不迁移配对 | 停源写者、保存；目标容器打开原目录继续，不为迁移重新clone | 暂停到首个正确操作、传输量、邻居延迟/完成量、旧写者结果 | 直接打开/复制同一状态 |

表中为机制测试的起点。RQ1正式容量档为1千/1万/10万，RQ3扩至128个客户端，RQ4扩至256个活跃工作区，具体方法与资源预算以[规模方案](scale.md)为准。

F1/F2逐文件定义沿用 [workloads.json](../../workloads.json)。每组先三批。保存/迁移实验用 `appendfsync always`；普通文件回放保留Redis常见的`everysec`配置，两种配置分组。JuiceFS不启用writeback。原生clone调用先按固定版本帮助和实际内容核验，不退化为全量复制后仍称clone。

比较BrewFS时使用相同客户端资源和同一套RustFS，分时运行、桶/前缀隔离；JuiceFS Redis与BrewFS元数据服务分别计入后端预算。缓存容量、初始准备和内容校验保持一致。单RustFS/单Redis组只测试客户端负载与客户端交接，不测后端容灾。

## 首轮数量与执行条件

```bash
mkdir -p runs/k8s
python3 scenarios/k8s-containers/scripts/campaign.py \
  --profile smoke --out runs/k8s/juicefs-smoke.json
```

默认只有JuiceFS：**6批、24次回放**。加入 `--systems juicefs brewfs` 后是12批、48次；固定64任务组 `--profile main` 每系统12批、768次；正式规模组 `--profile scale` 每系统12批、2,544次。四份trace的副本不计为新题。

smoke/main固定任务集合，只改并发；每次新工作区。main任务i的trace取`i%4`，worker取`(floor(i/4)+i%4+repeat-1)%4`。scale随客户端数增加任务量，每个worker恰好四种负载各一份；不能用它算固定工作量加速比。计划生成后冻结摘要、实际容器映射和软件版本。

先运行一个完整pytest，再运行其余三题。每题1800秒超时；批次上限`ceil(任务数/C)×1800+300`秒。失败保留，不自动调用模型。按实际占用决定是否扩大并发。

正式多容器批次需协调器统一发令并记录排队/完成事件；当前提供的是计划生成、单容器回放和完整指标/日志收集链路，**分布式任务调度与迁移执行器尚未接入**。不能靠手工依次启动声称同时开始。

结果范围：严格校验的文件回放，不是纯syscall计时或真实Agent完成时间；准备和全树校验会预热。跨容器是否跨节点按平台信息确认。已有脚本测试和本地完整轨迹记录见 [验证记录](validation/README.md)。
