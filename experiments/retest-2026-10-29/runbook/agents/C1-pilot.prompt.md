# C1 首次试跑的补充合同

先完整阅读[C1独立执行prompt](C1.prompt.md)及[数据交接](../DATA.zh.md)。当前只准备，不购机或启动pilot。

范围固定：pytest-full-02完整包，N=1→8，各一个Overlay/JuiceFS配对区组，共4 cohort，seed=42。另做单Agent语义资格；pilot不并入正式三区组，不用于发布性能排名。

新版ECS或同档自备Linux资源：2执行节点各32/128，RustFS64/256，Redis32/128，协调8/32，合计5节点、168vCPU/672GiB。数据盘、服务cgroup和Agent2/8见[部署手册](../DEPLOYMENT.zh.md)。不是旧120/480预算。

用scale_plan.py的--campaign ecs-20261101 --cluster C1 --profile pilot-8 --isolation docker生成；provider按调度单。两执行节点各4 Agent；from-plan输出5节点清单，缺地址/可信host-key/归属/预算时不执行。输入只需407606955B包并核完整SHA。

同步口径、正常缓存、Redis元数据、无SQLite服务fallback、不启Manager/Pack/hint/LLM模拟。先非空lower/FUSE内容资格，再按[观测协议](../OBSERVABILITY.zh.md)核8客户端密度采集开销和覆盖，再全体ready一次放行。逐轮保全；后续仅进入获准的c1-8，不自动扩到64/128。
