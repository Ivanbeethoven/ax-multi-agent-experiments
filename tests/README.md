# 测试入口

当前计划与配置检查：

```powershell
python -m unittest discover -s tests/cluster -v
```

覆盖六机资源、完整任务分配、相同工作量对照、单项参数消融、Packed/合并四组组合、C1 读写尺寸、挂载缓存与进程配置检查。全部使用本地临时数据，不启动服务器性能任务。

真实挂载和负载验证按 [SETUP](../docs/SETUP.md) 完成，原始输出保存到 [METRICS](../docs/METRICS.md) 指定目录。配置检查结果与性能结果分别记录。

根目录其它 `test_*.py` 对应仍保留的工具和历史套件。旧 trace、归档及仓库检查会计算校验和；按用户要求修改对应路径后再执行，不直接运行旧的整目录测试命令。
