# 四道 Verified 题：结果和 trace

本机 WSL / Docker，OpenCode 1.19.4 + deepseek-v4-flash。四道题按数据集难度标签预先选定，没有按模型表现换题。首轮三题通过；取消步数限制后，仅重跑未通过的 Django，仍未通过。

| 题目 | 首轮步骤 | 首轮评分 | 无步数上限重跑 | 最终结果 |
|---|---:|---|---|---|
| django__django-10554 | 40 | 未通过 | 70 步，未通过 | 未通过 |
| pytest-dev__pytest-10356 | 22 | 通过 | 未重跑 | 通过 |
| scikit-learn__scikit-learn-25102 | 40 | 通过 | 未重跑 | 通过 |
| sympy__sympy-13878 | 40 | 通过 | 未重跑 | 通过 |

Django 重跑从原始代码开始，没有带入上一轮补丁或评分答案。取消 OpenCode 的 steps 设置，将单题超时保护改为 3600 秒。实际运行 582.062 秒，正常退出，没有超时；两项目标测试仍然失败。不能把这次失败归因于 40 步上限，也不能据此说更多步骤一定无效。

成功以固定 SWE-bench 评分代码的报告为准。每题事先验证原始代码失败、参考补丁通过，再在另一个容器中让 Agent 解题。最终补丁在全新容器中评分。四题是小样本，3/4 不代表整个 Verified 的成功率，也不是 BrewFS 性能结果。

## 所有 trace 是否保存

**五次解题都有 Agent 事件日志和原生会话导出，但本次没有 POSIX/FUSE 文件操作 trace，也没有原始模型 HTTP 请求记录。**不能将这些日志直接输入 Rust 文件系统回放器。

| 尝试 | 日志工具调用数 | 会话工具调用数 | 输入、输出、状态核对 |
|---|---:|---:|---|
| Django 首轮 | 49 | 49 | 一致 |
| Pytest 首轮 | 28 | 28 | 一致 |
| Scikit-learn 首轮 | 49 | 49 | 一致 |
| SymPy 首轮 | 43 | 43 | 一致 |
| Django 重跑 | 73 | 73 | 一致 |

每次尝试保留：

- `agent-events.jsonl`：OpenCode 发出的文字、工具输入和返回结果、时间、步骤及 token 用量。
- `trace/ses_*.json`：原生会话导出，包括会话消息及各部分内容。`trace/manifest.json` 记录对应关系、核对结果和文件摘要。文件读取工具主动只读某个行范围时，保存的是实际返回范围。
- `prediction.patch`、`grading/report.json`、`grading/test_output.txt`：最终补丁及独立评分证据。
- 本机 `private-agent-state`：原生 SQLite 数据库、缓存和运行状态，未上传 GitHub。

Scikit-learn 首轮和 Django 重跑各有一次环境变量查询，工具返回中出现了模型 API 密钥。发布副本已替换为 `[REDACTED_API_KEY]`，保留调用本身和其余输出；脱敏次数见各目录 `summary.json`。内容核对在脱敏前进行，发布文件另外计算 SHA256。没有为了让记录看起来完整而删除失败、错误或这些查询。

首轮共 142 个可见模型步骤，重跑 70 个，共 212 个；工具调用共 242 次。OpenCode 步骤报告的总 tokens 为 9,371,172，其中缓存读取 9,049,600。两轮用量都计入，没有只统计成功尝试。辅助请求可能不在步骤报告中；当前配置没有可靠价格，账单金额未知，`cost=0` 不表示免费。

## 记录位置

- [首轮四题、评分和 trace](swebench-verified-hard4-20261023/README.md)
- [取消步数限制后的 Django 重跑](swebench-verified-hard4-unlimited-20261023/README.md)
- [运行、导出和发布方法](../scripts/swebench-local.zh.md)

本机原始记录位于 WSL 的 `/home/hxy/ax-swe4/20261023-hard4` 与 `/home/hxy/ax-swe4/20261023-hard4-unlimited`。准备阶段的问题单独保留在首轮目录的 `preflight-attempts`；不计作完成的模型解题尝试。

要做存储回放实验，还需在后续受控运行中启用文件系统记录器，固定初始文件状态，并验证回放结果。现有对话日志无法事后补回没有采集的底层读写。
