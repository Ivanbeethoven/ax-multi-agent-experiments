# 自备 Linux 服务器版本

与[ECS版本](../alicloud/PROVIDER.zh.md)使用相同C1–C4输入、baseline、方法、指标和逐步脚本；使用provider=provided，不调用云API，无需Kubernetes。硬件和角色见[部署手册](../../experiments/retest-2026-10-29/runbook/DEPLOYMENT.zh.md)，不能再用旧8核/32GiB、9/3台基础资格配置冒充64/128规模。

```bash
python3 bench/scale_plan.py --campaign ecs-20261101 --provider provided --isolation docker --profile main --cluster C2 --output "$RUN_DIR/plan.json"
```

```bash
python3 bench/experiment_inventory.py from-plan --plan "$RUN_DIR/plan.json" --output "$RUN_DIR/inventory.json"
```

C1/C3/C4替换cluster，128用expanded；独立VM使用vm并记录真实宿主映射。生成器不含ECS候选SKU，但保留同一角色CPU/内存/磁盘要求，输入/工作目录/地址待提供方填写。

| 提供内容 | 验收 |
|---|---|
| 服务器/VM | 真实资产号、CPU/线程/NUMA、内存、虚拟化与宿主关系，禁止隐蔽超卖 |
| 设备与目录 | 已批准独立数据设备、已挂载XFS/ext4、输入/缓存/结果工作区、所有权与容量；不默认允许格式化 |
| 网络与身份 | 私网、可信SSH host-key、专用SSH别名、必要有限sudo；严格host-key检查，有现成跳板才用 |
| 运行环境 | Linux、Python3.11+、FUSE3、cgroupv2；Docker/Compose按隔离选择提供；冻结工具和构建产物 |
| 空闲与配额 | 本轮独占时间、进程/挂载/服务归属、每角色cgroup与峰值余量，不能仅凭配置文件验收 |

先填inventory并静态检查，再逐机host_preflight；该检查按角色核CPU/内存、盘数与名义容量，网络/峰值/真实空闲状态另留证。机器不足时交付缺项，不自行租补。若合并角色或改变每宿主Agent密度，另立硬件配置及完整配对，不偷偷沿用原结果。

输入下载按[27份trace交接](../../experiments/retest-2026-10-29/runbook/DATA.zh.md)，也可使用同SHA的本地镜像；业务计时不依赖公网。阶段顺序与ECS相同：冻结→主机→服务→输入→原生挂载/资格→一个cohort→保全。

收尾只处理本轮登记的挂载、进程、容器、DB/key前缀和工作目录；**不删除服务器、不格式化、不清他人数据、不停原有服务**。共享机不得全局drop_caches，不能隔离冷态则标记不满足条件。provider之间独立campaign，硬件差异的数据不拼成一个配对区组。
