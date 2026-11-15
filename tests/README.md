# 代码检查

```powershell
python -m unittest discover -s tests/cluster -v
python -m unittest discover -s tests -p test_cloud_deploy.py -v
python -m unittest discover -s tests -p test_workspace_merge.py -v
```

覆盖现有挂载、资源配置、计时、指标、状态更新、内容比较和后端部署。Linux 文件语义相关检查在服务器执行。

历史套件的测试与源代码一起保存在 `archive/legacy-code/`。
