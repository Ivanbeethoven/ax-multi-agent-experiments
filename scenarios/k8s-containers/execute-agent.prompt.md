使用我提供的特权容器完成BrewFS与JuiceFS测试，直接执行并保存结果。

- 实验仓库：https://github.com/Ivanbeethoven/ax-multi-agent-experiments
- BrewFS：https://github.com/brewfs/brewfs；拉取执行开始时默认分支最新提交，记录完整SHA并固定到实验结束。
- 当前部署采用4节点RustFS、自建Redis、监控/协调器和若干worker。两系统共用同一S3入口，桶及元数据命名空间隔离，资源/缓存/持久化条件对齐。旧文档中的单RustFS示例仅作为启动参数参考。
- 先读[data-catalog.md](data-catalog.md)和[archives.json](archives.json)：已有两批7份输入；使用scripts/download_archives.py通过匿名HTTPS下载、校验，勿重新调用模型。全部实验数据已公开只读，下载无需阿里云身份。
- 下载前确认容器CPU/内存、FUSE能力、卷容量、私网互通和公网HTTPS连通。输入准备在计时前完成。
- 补齐协调器、ready屏障、并发/超时/失败保留和恢复；campaign.py现有计划冻结旧4份，使用新3份时先扩展并冻结新的任务表，不能把旧任务数直接当成7份测试。
- 先跑一个完整Pytest，再验证全部选定输入；用tools/full-fuse-replay核对操作数、读写字节、内容和最终树。
- 按资源执行固定工作量、4/16/64/128 worker扩展，以及workloads.json中的容量、分叉保存、共享数据和部分迁移。每组3批；真实消融只针对BrewFS已实现的机制。
- 开跑前接好资源、两系统、Redis、四个RustFS节点及原始日志采集；RustFS通过OTel进入Prometheus。缺指标填null，失败任务保留。
- 大结果及TSDB保存到oss://brewfs-agent-traces-20261023-4ccdae73/k8s-results/<唯一campaign>/，保留SHA256及manifest；脚本、配置模板与小型汇总推送到实验仓库分支，密钥不进Git。

交付真实结果、失败/未执行清单和复现命令。缺少外部访问条件时一次性说明，并继续不受影响的工作。
