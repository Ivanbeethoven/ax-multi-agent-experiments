# 写作取数表

`cases.csv` 列出本轮通过的 23 项，`metrics.csv` 为 472 条原始指标，`verification.json` 记录生成脚本及来源文件 SHA256。每行带有单位、阶段、来源 JSON、JSON Pointer 和计时范围，可直接按来源字段复核。

重新生成：在本实验目录执行 `python scripts/export-writing-metrics.py`。脚本只读取现有回执，逐项检查通过状态，确认 C1 I/O 9 项、B/q 3 项、C2 3 项、C3 2 项、C4 6 项，并逐行重新解析来源。缺失值不补零，旧失败与额外复测不混入这 23 项。

画图前先选 `group / case / phase / metric`，不要将整张表直接求和。`value` 保留原精度，`unit` 区分 s、ms、ns、B、B/s、operations、operations/s 与 ratio；换单位只在绘图时做。分位数不能跨用例求平均。

- C1：顺序/随机读写可按相同模式比较两系统；randrw 只有修复后的 BrewFS，不能补出 JuiceFS 对照。`fio_job_runtime` 与外层测量时间单列，fsync 延迟也单列。
- B/q：只作布局与读回正确性展示，单次请求耗时不能支持动态布局性能优势；缺失 q 不等于 0。
- C2：可画三系统回放时间。应用读写量与捕获负载的重复读取比例相同，后者不是缓存命中率。
- C3：可画各操作阶段耗时与延迟；操作前已全树校验，`cold-read` 仅保留原标签，不能当严格冷缓存。冷重挂校验单列。
- C4：fork/clone、发布准备、父子校验分别展示。`clone-check` 包含 clone；publication 内部阶段包含于总时间，不累加。stay/move 校验范围不同，不相减推导迁移开销。

本轮均为单次脚本与正确性验收（`formal_sample=false`）。472 行是不同指标，不是 472 次独立重复。新 Packed 优化候选的结果不在本表内。
