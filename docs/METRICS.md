# 指标与结果目录

直连 C1～C4 轮次使用 [campaign-direct.json](../configs/campaign-direct.json)，本地结果为 `results/cluster-direct/run-NNN/`，服务器为 `/srv/ax-cluster/runs/direct/run-NNN/`。沿用下述采集结构，增加 C4、`network/`、`network.json` 和实际服务 `endpoints.json`；网络采集见 [NETWORK](NETWORK.md)。

C1～C3 分组轮次采用 [campaign.json](../configs/campaign.json) 及下列位置。各轮从第一个未使用的 `run-NNN` 编号开始。

- 本地：`results/cluster-c1-c3/run-001/`。
- A366 汇总：`/srv/ax-cluster/runs/c1-c3/run-001/`。
- 其它节点：同一绝对路径下的 `hosts/<主机名>/`，每个节点只写自己的部分。

## 采集架构

A366 保存任务表和阶段事件；六台各一个采集进程，访问本机所有挂载的 metrics 端口，读取主机与任务 cgroup 数据。四个 RustFS 节点采集服务与物理盘指标，A355 采集 Redis。每 5 秒采样一次，准备/挂载/开始/应用结束/上传完成/内容检查/退出另写阶段事件。

每个挂载端口为 `20000 + 本机槽位`。采集按主机追加 JSONL，原始 Prometheus 文本分段压缩。不要按每次采样、每个挂载生成几十个零碎文件。已有 `bench/runtime/observe.py` 的读取接口可复用，批量输出需先完成 8 个挂载的小规模验证。

| 来源 | 必存指标 |
|---|---|
| fio / 回放 / 数据加载器 | 完成状态、操作类型与次数、成功字节、errno、每段时间、可合并延迟直方图、等待计划与实际等待 |
| BrewFS / JuiceFS | FUSE 请求与延迟、缓存容量/使用/命中、元数据请求、上传队列、未完成上传字节与操作、后台错误 |
| cgroup 与进程 | CPU、RSS、memory.current/peak/stat、io.stat、memory.events、FD、线程与 PSI |
| Redis | 内存、命令/Lua 调用、连接、延迟、AOF 状态、重启与淘汰数 |
| 四台 RustFS | GET/Range/HEAD/PUT/LIST 次数与字节、请求延迟、队列、EC CPU、实际 B/q、读写错误 |
| 主机 | CPU/内存、diskstats、网卡收发、磁盘剩余空间与 inode、UTC 与单调时钟 |

每个 C 都保存 `app_done` 和 `remote_ready` 时间。前者是应用操作完成；后者包含后台上传排空，以及新挂载实际读回。读回检查耗时独立记录，同时报告不含检查的上传完成时间。两次间隔至少 1 秒的零上传计数与进程正常退出一并保存。

## 字节与比例

应用字节取成功 read/write 返回值；S3 字节取实际请求/响应；物理字节取四台对象节点的磁盘计数差值。物理设备每次只求和一次。同盘上的客户端缓存和对象服务分别记录能归属的 I/O 与整盘总量。

读取放大 = 后端实际读取字节 / 应用成功读取字节。上传放大 = 实际 PUT 数据字节 / 应用成功写入字节。零分母记为 `null`。缓存使物理读取低于应用字节时，按实际值呈现。

重复读取比例 = `1 - 本任务每个文件内容版本的已读区间并集字节 / 累计成功读取字节`。同一文件被覆盖、截断或替换后按新内容版本统计；重命名沿用文件身份。跨工作区缓存复用另统计唯一对象区间和实际下载字节。输入 trace 的操作次数与真实 FUSE 请求次数分列。

短于 5 秒的阶段同时保存开始/结束计数快照；阶段总量直接取计数差值。延迟来自操作记录或服务直方图。多个 P99 不做算术平均。服务器重启或计数回退时分段计算，原始数据保留。

## 保存结构

```text
run-001/
  README.md                 版本、机器、完成数量与结论
  inventory.json            实际硬件、地址、路由、服务与目录
  versions.json             源码提交、构建命令、工具链、二进制路径
  plan/C1.json C2.json C3.json
  inputs.json               本次实际归档/事件/blobs/边界表路径与数量
  mounts/                   每个挂载的配置和实际生效记录
  commands.jsonl            主机、命令参数、起止时间、退出码、日志路径
  status.json               每个条件及重复的状态
  hosts/<host>/             原始系统、服务与挂载采样
  C1/<condition>/<system>/r1/  配置、原始负载输出、阶段、读回结果
  C2/<condition>/<system>/r1/
  C3/<condition>/<system>/r1/
  summary.csv               每次运行一行
  comparisons.csv           对照及消融的配对值
  figures/                  汇总图
```

结果状态使用 `planned/running/passed/failed/pending-feature`。重试建立 `attempt-02`，保留第一次运行。结束时从服务器取回原始指标、日志、参数、负载输出和汇总；输入大归档已存于 OSS，结果记录其实际路径和事件数量。

采集开销先用 8 个任务进行三对开/关比较，再在 128 个任务做一对检查，记录总时间与操作 P99。超过 5% 时减少重复采样后重新测量。远端原始数据只有在本地可正常读取、数量和字节长度一致之后才清理。
