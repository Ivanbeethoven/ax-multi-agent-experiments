# 补收历史 C1 校准原档

五组旧 C1 各有六轮校准。正式测量原档已有 OSS 下载索引；这里补收的是原校准的节点文件。两组顺序读、两组顺序写、JuiceFS 随机读，共 30 轮。旧 JuiceFS 也实际在 A 组运行，不能改从 B 组取档。

1. **固定原输入。** [草稿脚本](scripts/retain-old-c1-calibration-draft.py)只读核对旧状态、回执和采集配置。[验证回执](evidence/preparation/old-c1-calibration-draft-01/validation.json)给出原草稿的 SHA256，草稿留在协调节点。它不证明工作节点的原档仍齐全。
2. **等 A 组关闭。** 从实际原任务关闭回执和实时探针确认没有活动租约、Agent 进程、FUSE 挂载或上传债务。使用同一批的三角色主机、boot ID、数据盘证据。不能从旧草稿或系统服务退出码推定上述状态。
3. **一次收一组。** 在协调者新建路径，固定本地脚本、旧草稿、计划、SSH 配置、主机身份和新关闭回执的 SHA256。保留每次执行的输出和失败现场；不要覆盖既有路径。

协调者目录：`/srv/ax-pilot-control/preparation/c123-old-c1-retention-<新标识>/`。放入 [runner.py](scripts/retain-old-c1-calibration.py)、[node.py](scripts/retain-old-c1-calibration-node.py) 和 `request.reviewed.json`。请求用 `c123-old-c1-calibration-retention-request-v1`，字段以脚本校验为准。

```bash
python3 "$dir/runner.py" --apply-retention \
  --request "$dir/request.reviewed.json" --request-sha256 "$request_sha256" \
  --out "$dir/run"
```

脚本持有原 A 组资源锁，逐角色核验源、检查空间，并直接压缩到独立文件系统的 `/var/tmp`，不复制原始目录。每组应收回 12 份角色包（6 份客户端、各 3 份元数据和对象节点）以及协调机原始回执。只有协调机逐成员核验和 OSS 完整回读通过，才算封存完成。脚本不会重新测量、自动重试或删除源文件。
