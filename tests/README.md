# 验证入口

Linux / WSL 是完整测试环境：

```bash
python3 scripts/check_repository.py
python3 scripts/check_repository_archive.py
python3 -m unittest discover -s tests -v
```

默认只使用本地临时目录和模拟服务，不购机、不挂载 FUSE、不运行正式性能实验。需要真实 trace / Rust 二进制的集成测试会明确 skip；通过环境变量提供既有输入后才能运行，不能将 skip 当成通过。

仓库检查覆盖当前入口、非证据目录的文档链接及 pilot Python 脚本语法。冻结的 `evidence/` 文档保留原路径，不纳入当前链接检查；历史文件由归档校验器按固定 Git 提交和 SHA256 核验。浅克隆需先取完整历史。

2026-11-12 删除了 12 个仅测试退役脚本的测试文件，随原代码保存在 [Git 清单](../archive/README.md)。新增 `test_repository_history.py` 验证删除文件可恢复、哈希篡改拒绝及路径边界。当前回放、五项最小检查、配置与资源保护测试继续运行。

| 测试 | 内容 |
|---|---|
| `test_unified_suite.py` | 新C1–C4计划/native消融、baseline跨池并行/独立领取关闭/跨池配对、旧同池模式、原子共享资源占用、禁止重放、失败归档/关闭、恢复与gap排除；可选fio/小文件原生验证 |
| `test_suite_defaults.py` | 默认工作量/容量、非法参数提前拒绝、冻结缓存与pool绑定、memory store挂载参数复核、fio同字节错误参数拒绝 |
| `test_ssh_fleet.py` | 任意 SSH 节点/端口与槽位、代码摘要、宿主观测映射、同 cgroup 拒绝放行、trace 配置；可选真实 SSH 集成 |
| `test_experiment_steps.py`、`test_scale_plan.py` | 配对/资源隔离、8/64/128 规模、单步失败与保全 |
| `test_runtime_adapters.py` | 64真实本地worker屏障、失败不重投、原进程正常退出与活跃保护、分片并发、迁移lease门、真实/proc和HTTP采集、缺项/断档/重置/篡改拒绝、开销配对 |
| `test_deployment_plans.py` | C1–C4两provider角色和清单；本轮8 Agent的4+4放置另在scale测试覆盖 |
| `test_cloud_deploy.py` | RustFS 配置拒绝条件和 S3 接入/清理边界；名称沿用，代码同时适用于自备机 |
| `test_trace_*.py`、`test_full_trace.py`、`test_fs_trace_capture.py` | 输入/内容/终态、归档和默认关闭的固定总量随机等待 |
| `test_ml_*.py`、`test_warm_same_files.py` | 只读语料、真实 TiKV/Pack 路径参数与预热完整性 |
| `test_final_suite.py` | 挂载、缓存、版本、配对方案及历史结果格式的有效性约束 |

`test_fs_trace_capture.py` 按 Linux strace/POSIX 路径构造输入，不适用于原生 Windows；在 WSL 中验证。旧调度器、Ceph 和磁盘换挂的专属测试与被移除代码一起保存在 [历史归档](../archive/README.md)，未把它们计入当前测试通过数。

runtime的mountinfo/API失败边界使用受控替身；通过不代表真实BrewFS/JuiceFS挂载、跨节点原生会话或采集开销已验收。正式性能结果必须另附本批原生服务、内容及观测资格。

新统一实验的原生本地验证（小输入、真实fio与Rust文件IO；不启动云或FUSE）：

```bash
cargo test --manifest-path tools/fs-probe/Cargo.toml --bin smallfiles
cargo clippy --manifest-path tools/fs-probe/Cargo.toml --bin smallfiles -- -D warnings
cargo build --manifest-path tools/fs-probe/Cargo.toml --bin smallfiles
UNIFIED_NATIVE_TESTS=1 SMALLFILES_BINARY="$PWD/tools/fs-probe/target/debug/smallfiles" \
  python3 -m unittest discover -s tests -p test_unified_suite.py -v
```

Linux需要`/usr/bin/fio`真实工具；设置了上述变量却缺工具视为失败，不默默skip。Rust工具需支持`as_chunks`的工具链（Rust≥1.88）。验证包含字节损坏必须失败、父子隔离、全部原始阶段与重复输出拒绝。细节见 [系列文档](../experiments/unified-suite/README.md)。

真实 SSH 集成使用已有 OpenSSH server，只在 loopback 临时端口启动测试专用 sshd，随后回收。本例以 root 运行；测试覆盖固定 host key、远端代码摘要、observer 启停及被改动 host-key 文件的拒绝：

```bash
AX_TEST_LOCAL_SSH=1 python3 -m unittest discover -s tests -p test_ssh_fleet.py -v
```
