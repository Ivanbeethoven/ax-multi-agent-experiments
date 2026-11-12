# rbm：统一构建与最小模式验证

入口：从仓库根运行 `python3 -m bench.experiment_smoke`。所有输出目录必须是新路径。
模式参数由 `bench.experiment_modes` 解析，运行入口把 engine、hint、RustFS layout 和原生 fork/clone 传入真实探针。

- C1：1 MiB 顺序写读、16 个 4 KiB 随机读写、75:25 混合读写，写后 fsync，重挂后 SHA256。配置切换成功不等于实际 B/q 已生效；本探针不作布局或性能验收。
- C2：140 条合成回调的 Rust 严格回放，核初始树、最终树与重挂树。此输入不是完整 pytest trace，不作真实 Agent 工作量结果。
- C3：128 × 4 KiB，核初始文件、执行固定读写、重挂核修改文件。
- C4 派生：两文件 77 字节，原生 fork/clone、子读、覆盖及新建、父读回、子重挂。错误仍输出失败回执。
- C2 Overlay 开销、Pack 同引擎消融、C4 跨机干净迁移：尚未具备运行前提，写 blocked 回执并退出 2。单机重挂不能算跨机迁移通过。

BrewFS 默认 features 保留，并统一增加 workspace-overlay、observability、rustfs-ec-block-size-hint。
C1–C3 的统一构建来自上游 main ca07159。C4 原生 fork 另用已有发布/根目录链接数补丁的候选产物，并传入构建回执；它只用于功能验证，不能冒充统一性能产物。

单项示例（需 root、Docker、FUSE、Redis 和含 boto3 的 Python）：

~~~bash
sudo /srv/ax-data/workspace-adapter-20261111/venv/bin/python -m bench.experiment_smoke \
  --experiment C1-layout --mode dynamic-off \
  --brewfs /var/tmp/ax-mode-suite-20261112/bin/brewfs \
  --producer /var/tmp/ax-mode-suite-20261112/bin/packed_v3_snapshot_fixture \
  --smallfiles /srv/ax-bin/smallfiles --juicefs /usr/local/bin/juicefs \
  --brewfs-revision ca07159f3da4b670e4aeb878faef928e69bc1634 \
  --rustfs-image sha256:9750c5eaaac37e2d6ebaa8aaf39a4aa98a35fca5fba8de5ade26585123058806 \
  --out /var/tmp/ax-smoke-new
~~~

C2 另外传入 `--trace experiments/unified-suite/pilot-aliyun/fixtures/synthetic-140 --replay /srv/ax-bin/full-fuse-replay`。
相对输入路径请先转换为绝对路径。输出的 `receipt.json` 标记 passed / failed / blocked，原始日志在同一目录。
该状态仅指最小功能探针；不会修改正式 suite 的准入、计时条件或完成数。

整套串行运行用 python3 -m bench.experiment_smoke_matrix，传入相同的二进制、版本、镜像参数、
--trace（合成输入）及 --replay（回放器），并指定新的 --out；不再传 --experiment、--mode 或 --placement。
每项独立保留回执；任意 failed/blocked 时矩阵最终退出码为 2，但仍执行后续子项。

本轮[新二进制实测结果](evidence/preparation/rbm-unified-modes-20261112/README.zh.md)：
26 个模式，16 通过、1 失败、9 阻塞；包含一次目录检查器修复后的独立重跑。


C4 的旧失败已由[同步后的候选复验](evidence/preparation/rbm-c4-published-20261112/README.zh.md)解决。沿用上面命令的工具路径，改为：

~~~bash
--experiment C4-fork --mode brewfs-full \
--brewfs /home/dev/ax-brewfs-c4-build-20261112.GWrxnDJ8/nlink-02/brewfs-linux-x86_64 \
--brewfs-provenance experiments/unified-suite/pilot-aliyun/evidence/preparation/c4-local-packed-fork-candidate-01/root-nlink-build-receipt.json
~~~

这是替换参数片段，不是独立命令。JuiceFS 使用 `--mode juicefs`；结果目录每次换新。当前矩阵仍只有一个 `--brewfs` 输入，因此请将 C4 候选单独运行，不要用它替换整套统一构建。
