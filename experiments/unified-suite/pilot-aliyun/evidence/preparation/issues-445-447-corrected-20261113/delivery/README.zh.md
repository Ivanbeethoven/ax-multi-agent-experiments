# BrewFS Issues 445–447：最终实测证据

实际对照为基线 `4f982f45` 与候选 `c8f5f16a`，均包含相同的写回清理正确性修复。发布源码 `e2c964c5` 只增加 cfg(test) 的验值迭代器修正；对应源码一致性证明单列，未把二进制来源改写为 e2。

`archives/` 包含编译、正确性、标准回归、定向收益和诊断的原始记录。runtime 01–04 是已通过的实际正确性与旧夹具失败记录；05 是 DiskFull 失败及同一磁盘扩容；06/08 是完整标准正反序；07 是 cached 写入 CPU 对照；09 是前史后台合并的采样诊断（605 离线解析拒绝原样保留）；10 是新二进制的 114 次共享工作区和 24 次长行缓存对照；11 是固定4GiB三对写入与排空及最终进程清场。

标准601/603的历史状态 CPU 回退仍保留。固定量610–612只检验 fresh namespace 下前台写入与严格排空，不声称完整周期后台成本已结算。其完整 CPU 绝对中位数为10.84→10.86秒，完整耗时42.370633→42.329959秒；active/drain拆分原数均保留。

限定收益及准入边界见 `reviews/independent-final/` 和 `reviews/source-and-fixed-work/final-scope-review/`，不主张所有场景普遍加速。原始 perf 栈内存与实际大二进制只在 RBM 保全，来源、SHA和路径见 REFERENCES.json，未放入此提交包。旧402/403只保全针对性日志和5个sealed对象共20MiB，完整临时后端卷不在保全范围。

`delivery-manifest.json` 是完整包逐文件身份；较小的 transfer tar 故意省略协调者已缓存的大tar，按 excluded_cached_archives 清单补回同路径后须重新核全manifest。
