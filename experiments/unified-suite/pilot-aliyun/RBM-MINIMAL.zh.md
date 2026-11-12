# rbm：统一构建与最小模式验证

入口：从仓库根运行 `python3 -m bench.experiment_smoke`。所有输出目录必须是新路径。
模式参数由 `bench.experiment_modes` 解析，运行入口把 engine、hint、RustFS layout 和原生 fork/clone 传入真实探针。

- C1：1 MiB 顺序写读、16 个 4 KiB 随机读写、75:25 混合读写，写后 fsync，重挂后 SHA256。配置切换成功不等于实际 B/q 已生效；本探针不作布局或性能验收。
- C2：140 条合成回调的 Rust 严格回放，核初始树、最终树与重挂树。此输入不是完整 pytest trace，不作真实 Agent 工作量结果。
- C3：128 × 4 KiB，核初始文件、执行固定读写、重挂核修改文件。
- C4 派生：两文件 77 字节，原生 fork/clone、子读、覆盖及新建、父读回、子重挂。错误仍输出失败回执。
- C2 Overlay 开销、Pack 同引擎消融、C4 跨机干净迁移：尚未具备运行前提，写 blocked 回执并退出 2。单机重挂不能算跨机迁移通过。

BrewFS 默认 features 保留，并统一增加 workspace-overlay、observability、rustfs-ec-block-size-hint。
代码版本来自上游 main ca07159，未混入停工的 v1 修复草稿。新产物独立复制并记录 SHA；一批模式共用同一套产物。

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
