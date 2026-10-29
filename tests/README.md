# 验证入口

Linux / WSL 是完整测试环境：

```bash
python3 scripts/check_repository.py
python3 scripts/check_repository_archive.py
python3 -m unittest discover -s tests -v
```

默认只使用本地临时目录和模拟服务，不购机、不挂载 FUSE、不运行正式性能实验。需要真实 trace / Rust 二进制的集成测试会明确 skip；通过环境变量提供既有输入后才能运行，不能将 skip 当成通过。

| 测试 | 内容 |
|---|---|
| `test_experiment_steps.py`、`test_scale_plan.py` | 两种服务器环境、配对/资源隔离、64/128 规模、单步失败与保全 |
| `test_cloud_deploy.py` | RustFS 配置拒绝条件和 S3 接入/清理边界；名称沿用，代码同时适用于自备机 |
| `test_trace_*.py`、`test_full_trace.py`、`test_fs_trace_capture.py` | 输入/内容/终态、归档和默认关闭的固定总量随机等待 |
| `test_ml_*.py`、`test_warm_same_files.py` | 只读语料、真实 TiKV/Pack 路径参数与预热完整性 |
| `test_final_suite.py` | 挂载、缓存、版本、配对方案及历史结果格式的有效性约束 |

`test_fs_trace_capture.py` 按 Linux strace/POSIX 路径构造输入，不适用于原生 Windows；在 WSL 中验证。旧调度器、Ceph 和磁盘换挂的专属测试与被移除代码一起保存在 [历史归档](../archive/README.md)，未把它们计入当前测试通过数。
