# 独立pool的Redis和RustFS

> 历史协议，仅用于原冻结计划；新实验见[当前方案](../current/README.zh.md)。

每池使用独立metadata和objects主机、数据盘及服务。共用原跳板的私网代理和镜像registry；不共用Redis、RustFS或采集器。准备脚本只操作明确指定的节点，不创建实例。

`inventory.json`可为旧单池`{"pool_id":"pool-a","roles":{...}}`，或多池`{"pools":[{"pool_id":"pool-a","roles":{...}},{"pool_id":"pool-b","roles":{...}}]}`。每个role包含实际`instance_id/private_ip/hostname/data_disk_ids`；节点spec沿用`provision-node.py`输出字段。下面命令在对应新主机以root执行，所有输出目录必须未使用。

```bash
# metadata：先完成新数据盘格式化、软件包安装。
python3 configure-node.py redis --spec "$METADATA_SPEC" \
  --inventory "$INVENTORY" --pool pool-b --private-ip "$METADATA_IP" --out "$REDIS_OUT"

# objects：先完成四盘格式化及configure-node.py object-paths。
python3 deploy-objects.py --assets "$ASSETS" --node-spec "$OBJECTS_SPEC" \
  --inventory "$INVENTORY" --pool pool-b --out "$RUSTFS_OUT"

# 新campaign：装包完成且升级服务退出后，保留原状态并暂停定时维护。
python3 freeze-maintenance.py --spec "$NODE_SPEC" --out "$MAINTENANCE_OUT"
```

RustFS端点、health探测及采集cluster标记使用inventory中的objects地址；部署前核本机ECS身份、hostname、IP归属以及四个独立磁盘绑定。镜像仍从`10.20.0.10:15000`按digest拉取，并核OCI提交和镜像内二进制SHA。CPU、内存、四个layout开关和1秒原生指标周期保持原配置。

Redis监听地址必须与inventory及本机接口一致，配置文件用节点campaign命名。服务名`ax-pilot-redis`保持不变，因为两池位于不同物理主机。RustFS的OTLP目标仍为各自objects主机上的`127.0.0.1:4318`；每池另启独立采集器并绑定本池cluster ID。

旧pool-a的`deploy-objects.py --assets ... --out ...`调用保留原hostname、地址及project名称。新pool必须显式传节点spec、inventory和pool；只换名称不能获得独立资源。

准备时用`run-preparation-step.py`逐步保留实际命令、PID、原始输出和退出码；失败不重跑，同名步骤拒绝覆盖。它对应本次bootstrap使用的`run-step.py`，可在新主机复制脚本后执行：

```bash
python3 run-preparation-step.py --steps-root "$PREPARATION/steps" --name inspect -- \
  python3 provision-node.py inspect --spec "$NODE_SPEC" --out "$PREPARATION/inspect"
```

其余准备按顺序使用`provision-node.py inspect/format/packages`→`freeze-maintenance.py`→`download-assets.py`→角色配置；新二进制用`install-deployment.py`安装到新目录，控制代码用`install-code.py`安装到新目录。私网分发沿用协调者SSH公钥、严格主机key核验和SCP，复制后核文件SHA；命令参数只写私有env路径，凭据值、私钥和令牌不进入命令记录。

每池另做一次实际RustFS布局资格，不复用另一池的结果。单独复制的工具从冻结runtime读取observer与layout decoder；新pool必须显式传匹配服务回执的S3地址。四盘路径取实际compose并与运行容器绑定核对。

```bash
python3 qualify-rustfs-local.py --cloud --runtime-repo "$FROZEN_RUNTIME" \
  --services "$RUSTFS_OUT" --build-root "$ASSETS" --collector-name "$COLLECTOR_NAME" \
  --s3-endpoint "$S3_ENDPOINT" --compose "$RUSTFS_OUT/deployment/rustfs1/compose.json" \
  --out "$NEW_LAYOUT_QUALIFICATION"
```
