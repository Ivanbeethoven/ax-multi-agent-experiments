这是新三节点磁盘矩阵的绘图入口，与先前 e 系列三方法图分开。[完整矩阵图与简报](../summary/summary.zh.md)已生成，12 项完整回放、自动 QA 与 root 实际视觉检查均通过。100 GiB PL0／500 GiB PL0 已释放；500 GiB PL1 为额外原版 overlay 验证保留，尚未释放。性能仍未验收。

使用本任务既定 Python 环境。传入三个节点目录、校准摘要及仓库根目录，以真实 archive／receipt／checkpoint／status、combined verification 和资源状态执行只读检查：

```text
python generate_matrix.py --check
  --node 100g-pl0=NODE_100G_PL0
  --node 500g-pl0=NODE_500G_PL0
  --node 500g-pl1=NODE_500G_PL1
  --calibration-summary CALIBRATION_OBSERVATION_SUMMARY
  --source-root REPOSITORY_ROOT
```

缺失时返回 `pending` 和具体证据项，不写图、报告或 CSV。500 GiB PL1 为 root 已授权的额外原版 overlay 验证保留时，可显式传 `--retention-evidence RETENTION_RECORD`；该状态输出为“尚未释放”，其他节点仍要求精确释放。额外原版对照不进入 3×4 矩阵。

仅在 root 明确最终放行后，添加 `--render --render-authorized --out NEW_OUTPUT_DIRECTORY --skill-scripts NATURE_FIGURE_SCRIPTS`。禁止使用中途耗时或模拟值。输出为单面板 PNG／editable SVG／PDF、12 行回放 CSV、18 行独立 fio CSV（含三次预填充）、准备阶段 CSV、紧凑中文报告与必要公开证据；大观测日志以已验证 archive SHA 标识，不建立无效公开链接。

每次重新渲染后必须执行 `validate_figure.py`、`audit_pdf_text.py --min-pt 5`、`audit_figure_collisions.py`，检查 editable SVG、CSV 与源值、全部本地链接和增量凭据扫描，并实际查看完整 PNG 与 PDF。当前图全部检查已通过；唯一源码警告是没有未被要求的 TIFF，最终 PDF 最小字号 7 pt、碰撞 0 fail／0 warn。

完整规范见 [figure-contract.md](figure-contract.md)。每单元 n=1、CPU 8475B／8575C 混杂、恢复间隔、未清宿主页缓存、performance mode 的正确性限制及 C4 未通过性能验收都必须保留。
