# rbm：C4 原生 fork / clone 正确性验证

同一份非空输入：根目录 1 个文本、子目录 1 个二进制文件，共 77 字节。父目录读回后，BrewFS 按精确不可变 revision 执行原生 fork，JuiceFS 执行原生目录 clone。子工作区先读回，再写同名文件并新建一个文件（均 fsync）；卸载后用新的缓存目录分别重挂检查父、子文件 SHA256。两边都用隔离的本机 Redis 和 RustFS，没有新增机器、没有计时，也没有用完整复制代替派生。JuiceFS 初始父目录从相同输入导入。

| 核验 | BrewFS 原生 fork | JuiceFS 原生 clone |
| --- | --- | --- |
| 子工作区读到父数据 | 失败：2 个文件变 0 个 | 通过 |
| 子写后父内容不变 | 通过；BrewFS 同名写在空子目录创建文件，没有覆盖继承文件 | 通过 |
| 子写在重挂后保留 | 通过 | 通过 |
| 子重挂后完整内容正确 | 失败：缺未修改的父文件 | 通过 |

BrewFS fork 返回独立工作区 ID，revision 相同，但 Redis 中只有父工作区的 Packed lower 绑定。此次复现后仍不能将 BrewFS 原生 fork 纳入正式对比。所有挂载正常退出；后端没有重启，临时服务和 scratch 已清理。

BrewFS 使用运行前核对的上游 `brewfs/brewfs main` HEAD `ca07159f3da4b670e4aeb878faef928e69bc1634`，与 rbm 现有产物的源码 checkout 一致。JuiceFS 为 1.4.2。二进制哈希和 RustFS 镜像 ID 见[回执](receipt.json)。完整运行日志留在 `rbm:/var/tmp/ax-rbm-c4-native-01/`。

复跑需要 root、FUSE、Docker、Redis 和含 boto3 的 Python。输出目录必须不存在；退出码 1 表示至少一侧未通过，请查看回执中分开的判定项。

```bash
sudo /srv/ax-data/workspace-adapter-20261111/venv/bin/python \
  experiments/unified-suite/pilot-aliyun/evidence/preparation/rbm-c4-native-20261112/smoke.py \
  --brewfs /srv/build-ca07159/target/release/brewfs \
  --producer /srv/build-ca07159/target/release/packed_v3_snapshot_fixture \
  --brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634 \
  --juicefs /usr/local/bin/juicefs \
  --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806 \
  --out /var/tmp/ax-rbm-c4-native-next
```
