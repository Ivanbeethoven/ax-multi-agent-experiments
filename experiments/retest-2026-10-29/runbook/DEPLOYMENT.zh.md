# 实验环境交付

运行层统一使用 SSH。执行方提供容器、VM或服务器以及共享后端；实验Agent通过[SSH-RUN](SSH-RUN.zh.md)准备、检查和执行单个批次。

## 交付项

| 项 | 内容 |
|---|---|
| SSH | 每个环境的alias、用户、端口、可信host-key；需要时提供ProxyJump |
| 身份 | 真实容器/VM标识、共享宿主关系、所有权与允许操作范围 |
| 工具 | Python 3.11+、同版实验代码、固定SHA的测量程序与文件系统客户端 |
| FUSE环境 | /dev/fuse、挂载能力、独立可写目录、cgroup v2；C3请求节点无需FUSE |
| 资源 | Agent及服务的实际CPU/内存/存储配额、数据路径、容量/inode和共享上限 |
| 后端 | RustFS及Redis或TiKV端点、版本、持久性、命名空间与受保护凭据 |
| 观测 | 容器指标与宿主采集入口、原生服务指标、结果留存路径 |

C1每Agent客户端和回放器共享2CPU/8GiB、swap=0。多个环境共享宿主时核总配额、缓存和后台进程；后端规格按峰值与负载资格确定。RustFS四盘EC布局需要真实设备证据，四个目录只表示四个路径。

## 环境来源

| 来源 | 准备路径 | 执行路径 |
|---|---|---|
| 已配置SSH的容器 | 填fleet，直接使用已交付隔离 | provider=ssh |
| 已有服务器/VM | 填fleet；管理员配置独立cgroup或已有容器 | provider=ssh |
| 阿里云ECS | [云资源准备](../../../infra/alicloud/PROVIDER.zh.md)，采购另行授权 | 配置SSH后使用同一runtime |

SSH脚本不调用云API，不创建容器、格式化设备或删除主机。云资源和服务部署步骤独立于测量代码。

## 版本与历史方案

版本准备遵循根目录[AGENTS](../../../AGENTS.md)：每批同步指定RustFS分支最新代码，SHA/digest只记录实际版本。dynamic服务端默认开启、BrewFS默认auto；实际hint与持久化B/q须核验，auto尚不是完整多档自适应。

旧ECS/自备固定角色矩阵由scale_plan的aliyun/provided选项保留复现；[旧C1阿里云预算](../C1-ALIYUN.zh.md)含已暂停的5台168核配置。新的SSH方案只生成工作量矩阵，实际放置来自fleet。
