# 执行入口

使用 [阿里云运行说明](../infra/alicloud/RUN.zh.md) 和 [最终五个 RQ](FINAL-PLAN.zh.md)。

旧 W1–W4 Bash 执行器已移除，避免继续生成伪冷暖/未切换 TTL 的结果；历史版本可在 Git 提交 `889262e` 找到。

- `harness/aliyun_suite.py`：真实挂载、Rust 负载、Packed 构建/查询、已有固定版本 fork、原 Rust trace 回放。
- `infra/alicloud/calibrate.py`：数据盘预填充、单盘与并发 fio 标定。
- `infra/alicloud/dispatch.py`：显式任务清单跨主机派发和收集；SSH 断开不杀远端实验，不自动重投。
- `harness/final_plan.py`：五个 RQ 的配对任务表，默认不放行。
- `harness/packed_cost.py`：同单位固定成本、复用和更新成本核算。

`pipeline/run-all.sh` 只是新入口的兼容路径，参数使用新格式。它不会把旧 `--config c3` 命令静默解释为新实验。

验证：

```bash
python3 -m unittest discover -s tests -p test_final_suite.py -v
python3 paper-design/pipeline/validate.py --self-test
```

历史 CSV 保持原样。更严格的校验发现 C3 W2 的九行字节数字段无效；它作为负例保留。汇总只统计 status=ok 的有效格式数据，同时展示全部状态数量；格式通过仍不等于机制/持久性资格通过。没有本批云端端到端通过记录前，不能宣称所有正式实验已就绪。
