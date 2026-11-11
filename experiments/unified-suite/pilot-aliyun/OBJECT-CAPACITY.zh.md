# 百万文件实验的磁盘准备

现有四块60GiB对象数据盘保留。4KiB对象探针已测得每盘约8KiB实际分配；一份百万文件base加七份完整副本，至少每盘65,536,000,000B，超过现有容量。先测首份完整base的实际增量，再按剩余副本数和20%余量决定最终容量。

已只读核对原实例、系统盘和四块数据盘，并取得扩到每盘256GiB的备选报价。对象节点含四盘的时价由¥2.346变为¥3.9924，增加¥1.6464/小时；**未执行扩容，也未改变当前费用单价**。这是当前整机配置询价，实际扩容费用及生效时间仍须另核。[清单与报价](evidence/preparation/object-growth-option-01/growth-plan.json)、[七项身份/配置检查](evidence/preparation/object-growth-option-01/checks.json)、[完整原始包](evidence/preparation/object-growth-option-01/retention.json)已保存。

只读准备命令：

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/plan-object-growth.py \
  --release-set "$OWNED_OBJECT_SET" --release-set-sha256 "$OWNED_OBJECT_SET_SHA256" \
  --desired-gib 256 --out "$NEW_PLAN_DIRECTORY"
```

`OWNED_OBJECT_SET`使用`release-evidence.py freeze`生成的原对象节点清单。工具重新查询真实ECS资源并询价，拒绝原有实例、其他可用区、混入系统盘、磁盘数量或序列号变化、其他磁盘类型以及不增加容量的请求。它不执行worker命令，也不发送扩盘请求。

实际扩容安排在C1/C2原配对批次及所有校准结束、原始记录独立归档后。先确认无活动任务、租约、挂载或采集器，再核四盘的设备序列号、ext4 UUID与挂载关系。仅对这四块原盘扩容；不重新格式化。云端容量增加后，还须扩展ext4，并核容量、UUID及数据保留，生成新的C3环境和容量资格；旧C1/C2记录保持原容量。当前只有只读准备入口，实际扩盘及文件系统扩展尚未执行。

依据：[阿里云ResizeDisk API](https://www.alibabacloud.com/help/en/ecs/developer-reference/api-ecs-2014-05-26-resizedisk)的`Type=online`及[Linux磁盘扩容](https://help.aliyun.com/en/ecs/user-guide/resize-linux-cloud-disks)。API扩容不会自动扩大文件系统，两个步骤分别核验。
