# rbm：BrewFS / JuiceFS 挂载冒烟验证

2026-11-12 在同一台 rbm 上串行完成。私有 Redis 和 RustFS 只绑定 loopback；两套文件系统使用独立桶和 Redis DB。临时服务、挂载和本轮数据已清理。没有开新机器。

同一份 128 × 4 KiB 输入：BrewFS workspace-v1 从 Packed-v3 导入，JuiceFS 1.4.2 从同一目录导入。两边都校验全部初始文件，执行相同 smallfiles 读写（432 次校验读、64 次写），正常卸载，换新客户端缓存目录重挂，再核对 16 个写过的文件并卸载。四次卸载退出码均为 0。后端全程保持在线；这是脚本验证，不是后端重启恢复、正式性能、C4 fork 或 Overlay 验收。

[成功回执](receipt.json) · [复跑脚本](smoke.py)。BrewFS 本机产物对应 checkout ca07159；RustFS 镜像 revision f4ed202508f2153afb7956bae6f7799b60eccd00。产物哈希以回执为准；没有验证这些产物是上游最新分支。

复跑需 root、FUSE、Docker、redis-server、redis-cli，以及装有 boto3 的 Python。使用新输出目录：

```bash
sudo /srv/ax-data/workspace-adapter-20261111/venv/bin/python \
  experiments/unified-suite/pilot-aliyun/evidence/preparation/rbm-mount-lifecycle-20261112/smoke.py \
  --brewfs /srv/build-ca07159/target/release/brewfs \
  --producer /srv/build-ca07159/target/release/packed_v3_snapshot_fixture \
  --juicefs /usr/local/bin/juicefs --smallfiles /srv/ax-bin/smallfiles \
  --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806 \
  --out /var/tmp/ax-rbm-lifecycle-next
```

排查记录：Docker 未识别短镜像 ID，改用完整 ID；S3 就绪检查的 Python SDK 未收到临时凭据，改为显式传入；BrewFS 初始化 stdout 带附加输出，改为提取含 workspace_id 的 JSON 对象。成功轮完整输出在 rbm:/var/tmp/ax-rbm-lifecycle-05/。
