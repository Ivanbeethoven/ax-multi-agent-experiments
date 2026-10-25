# 格式与验收

目录：

```text
task/
  initial/                    冻结的repo与state
  initial-tree.json
  backing/                    采集完成后的真实状态
  mount/                      记录挂载；完成后已卸载
  capture/events.jsonl
  capture/blobs/<sha256>       精确写入内容
  agent-events.jsonl           Agent自己的事件日志
  agent-exit.json
  selection.json              题目、镜像内容ID、版本与限制
  collection-policy.json
  preflight/                  小任务的gate与回放记录
  replayed/                   Rust回放后的文件
  replay.json
  final-tree.json
  verification.json
  metrics.json
  result.json
```

events.jsonl每行字段为seq、op、args、result、errno、pid、uid、gid、start_ns、end_ns。seq从1连续递增。时间为采集机的monotonic_ns，不能与另一机器直接相减。read的result记录实际返回len和sha256；write参数用blob摘要和len引用原字节；失败操作保存原errno。没有抽样。

重复读取：相同文件身份和偏移区域，在上次读取后没有被覆盖，又被读取的实际字节/全部实际读取字节。rename/link保持文件身份；覆盖写使相应区域失效，删除后重建另算。

通过条件：

- 预检gate.passed=true，Agent启动前两条路径确认属于FUSE；记录器覆盖任务开始至整体停止。
- trace有正常init/destroy，seq连续，无缺失blob、未知操作或读内容差异。
- replay.valid=true，remaining_handles=0；operations等于metrics.callbacks，实际读写byte一致。
- 初始树与冻结清单一致；最终路径、类型、文件字节、权限、软链接目标一致；hardlink拓扑另验。
- 若有OpenCode数据库，复制db、wal、shm到临时目录再执行PRAGMA quick_check，检查session/message/part表；不能直接打开原始数据库导致SQLite恢复/改写它。
- 每个完成案例有result.json；filesystem-only的resolved=null。Agent退出124或没有修好题目仍可保留有效文件trace；缺少正常收尾的记录标不完整。

getattr不要求设备号、inode数值、所有者、时间戳和目录分配大小跨机器相同。statfs动态容量不作相等要求。单独比较这些值不能替代内容验收。

来源样例：pytest共1,747,109个回调，实际读281,774,624字节、写194,706,661字节，重复读比例约89.74%。这是既有记录的核验值，不是新机器必须达到的性能指标。

本包的本地验证记录在`assets/validation.json`：Rust release构建、真实FUSE预检299次操作，以及验收/归档脚本的合成案例检查；未调用模型。固定数据下载地址与SHA256也已实际检查。

归档的已知密钥扫描不等于覆盖所有秘密。保留数据真实字节；若发现密钥，停止发布并记录问题。归档本地完成后，上传目标由用户指定，不在采集结束时自动上传。
