# 六机直连网络

四组实验使用服务器 `eth0` 的同网段直连。配置入口为 [network.json](../configs/network.json)，本轮范围为 [experiment-suite.json](../configs/experiment-suite.json)。SSH 别名用于管理连接，存储流量使用下表地址。

| 主机 | SSH 别名 | eth0 直连地址 | 角色 |
|---|---|---|---|
| A366 | idcai-60001 | 175.168.3.66 | 协调、编译、客户端 |
| A355 | idcai-60525 | 175.168.3.55 | Redis、客户端 |
| A672 | idcai-60103 | 175.168.6.72 | RustFS、客户端 |
| A601 | idcai-60439 | 175.168.6.1 | RustFS、客户端 |
| A635 | idcai-60533 | 175.168.6.35 | RustFS、客户端 |
| A368 | idcai-60555 | 175.168.3.68 | RustFS、客户端 |

这里的内网指服务商分配给六机的同网段直连；`175.168.*` 不属于 RFC1918 私有地址。服务绑定上述地址，访问范围限制为六台主机；Redis 和 RustFS 启用认证。是否经过直连由实际路由和连接记录确认。

## 网络准备

1. 保存六机 `ip -br address`、`ip route`、MTU、网卡速率、`ethtool -k eth0` 与 `ethtool -S eth0`。对每个跨机目标执行 `ip route get <目标地址> from <本机地址>`，应显示 `dev eth0`；同机访问记录为 local/lo。
2. 使用 `infra/idcai/compare_network_paths.py --routes direct` 测完 30 个方向，每方向 5 秒、4 个 TCP 连接。每次只测一个方向，保存客户端和接收端 JSON、TCP 重传与 CPU。
3. A366、A355 接收方向再做两轮 direct/WireGuard 配对，第二轮交换顺序。诊断结束后退出临时 iperf3 服务，再开始文件系统计时。
4. 记录 RTT 和丢包。正式测试期间保持 MTU、offload、拥塞控制与网卡队列配置一致；如调整网络，相关配对条件重新运行。

2026-11-15 的已完成样本：A355→A366 直连 9.412 Gbit/s、WireGuard 1.849 Gbit/s；A672→A366 直连 8.904 Gbit/s。原始记录在 `results/network/run-002/summary.json`。这些数值用于安排直连测试，正式轮次保存自己的网络测量。

## 服务与客户端地址

- **Redis**：A355 的 `175.168.3.55`，优先使用空闲的 6379 端口。实际端口、数据库及不含凭据的地址写入本轮 `endpoints.json`；BrewFS 与两种 JuiceFS 使用本轮统一元数据服务、各自卷名与前缀。
- **RustFS**：A672/A601/A635/A368 四节点各一盘，EC 2+2。部署 spec 的 `private_subnet` 填 `175.168.0.0/16`，每节点 `private_ip` 填上表地址，`network_mode=host`。现有生成器支持这组配置。
- **节点间通信**：四台容器内 `rustfs1`～`rustfs4` 必须分别解析为 `175.168.6.72/.1/.35` 和 `175.168.3.68`。检查生成的 `extra_hosts`、`RUSTFS_ADDRESS`、`RUSTFS_VOLUMES` 和实际 TCP 连接。
- **S3 客户端**：优先端口 9000，四个直连 endpoint 按工作区编号轮转。BrewFS 数据配置、JuiceFS 卷内保存的存储地址、测试工具和观测器采用相同分配。某客户端访问本机 RustFS 时标记为 local，三个系统使用相同安排。
- **metrics**：采集器在服务所在机器读取本地端口；汇总、输入分发和结果传输使用直连地址。下载、归档、编译与性能计时分别安排。

已有服务先确认所属任务和空闲端口。新轮次建立独立卷、对象前缀、缓存和结果目录；服务 endpoint 写入配置后，通过运行进程的连接确认生效。检查范围包含客户端到 Redis、客户端到 S3、RustFS 节点之间。WireGuard 只用于网络诊断对照，正式存储连接采用直连地址。

服务监听端口与其它任务冲突时，将实际可用端口统一写入 `endpoints.json` 并生成全部对应配置。RustFS 生成器当前使用 9000；选用其它端口时同时更新监听地址、四节点 URL 和客户端配置，并完成四节点读写检查。

## 指标与网络饱和

每 5 秒采集 `eth0` 的 RX/TX 字节、包、drop、error，TCP RetransSegs、softirq CPU、服务进程 CPU、连接数；`axwg0` 字节同时保存。阶段开始和结束保存计数快照以及任务进程连接。`axwg0` 上其它任务的流量单独注明。

网卡吞吐按实际字节差值和经过时间计算，分别报告 Gbit/s 与 MB/s；保留每节点数据。对象请求字节、应用字节和网卡字节分别统计。同机 local 请求会消耗 S3 和磁盘资源，其流量不计入 eth0。

C2 的 8/32/64/128 个工作区依次执行。每档报告应用吞吐、S3 吞吐、网卡吞吐、Redis 请求、CPU 和磁盘利用率。根据这些指标说明吞吐趋于稳定时主要消耗哪类资源。全部系统采用相同端点分配、主机资源和网络设置。
