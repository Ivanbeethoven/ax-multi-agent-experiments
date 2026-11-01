# 阿里云 ECS 准备入口

本轮使用 **ecs-20261101**，四组的实验设置、SKU、角色、容量和操作顺序统一见[部署手册](../../experiments/retest-2026-10-29/runbook/DEPLOYMENT.zh.md)。这是ECS＋Linux上的Docker/独立VM方案，无需ACK/Kubernetes；当前不购机或开跑。

每组单独生成，示例C2：

```bash
python3 bench/scale_plan.py --campaign ecs-20261101 --provider aliyun --isolation docker --profile main --cluster C2 --output "$RUN_DIR/plan.json"
```

```bash
python3 bench/experiment_inventory.py from-plan --plan "$RUN_DIR/plan.json" --output "$RUN_DIR/inventory.json"
```

计划输出候选ECS规格和逐角色磁盘目标；inventory只含待填身份，不调用阿里云API。旧template的9/3节点仅供历史基础资格，不用于本轮64/128。

1. 执行方填写地域、可用区、实际库存/报价、实例与云盘性能限额、配额、总费用上限及运行范围；先核本轮已有资源清单，避免重复创建。
2. 将具体资源申请作为独立步骤审阅后执行，保存实例/盘/SG/来源规则/vSwitch/keypair/桶的精确ID、所有权及预先存在标志。四组服务池隔离，同组配对资源相同。
3. 尽量只有一个获批公网管理入口，其他节点只走私网/ProxyJump；host-key独立核验，来源限定已批准地址，不开宽泛网段。产物/数据只下载一次，经私网或同地域OSS internal分发。
4. inventory check后，在每个实际节点运行host_preflight，另验网络、空闲状态、峰值容量和真实cgroup。云盘独立不证明物理故障域独立；C4缺宿主证据时只称跨实例。
5. 后端配置使用[generate_rustfs](../rustfs/generate_rustfs.py)，明确experiment.protocol_profile=ecs-20261101及56CPU/224GiB服务预算；生成、检查、启动分别执行，实际B/q和四开关留证。
6. 不自动重试抢占中断、不自动补购。保全核SHA、确认本组进程/FUSE/服务结束后，再按精确ID独立释放并查询确认；共用与预先存在资源保留。

没有新建一键Terraform/总dispatcher；测量脚本不包含购买或释放行为。旧控制器继续留在[归档](../../archive/README.md)。
