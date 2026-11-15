# Aliyun EC 12+4 Range GET 图表

论文使用 `source/12plus4-author-table.json` 中的七档请求：固定 1 MiB、
Hint 256 KiB、Hint 64 KiB。左图显示请求吞吐率，右图显示 P50/P99；
百分比标注使用 16 KiB 请求。单栏两个并列子图，宽 240 pt，字体 10 pt。

`data.json`、`source/summary.tsv`、`source/rounds/` 和压缩 Warp JSON
保留公开报告的数据，来源提交为
[`a00ad83`](https://github.com/marshawcoco/rustfs/tree/a00ad83aa555535ad6517e38bd9367d858b3bdc0)。
归档含 12+4 和 8+8；论文只展示 12+4，每工况一次。
12+4 基线在 4 KiB 工况有 16 次启动错误，图注保留该信息。
完整对象吞吐率、预测值和未测优化收益不进入本图。

```powershell
python render_12plus4.py --paper <paper-root> --qa-tools <figure-qa-scripts>
```

需要 matplotlib，以及 nature-figure skill 的 `scripts/` QA 工具目录。
脚本核对 49 个显示值与归档并复算百分比，输出 PDF、可编辑 SVG、PNG
到论文的 `figures/pilot/`，核验记录保存在 `qa/`。
`export_12plus4.py` 是表格导出工具，也是百分比计算的共享模块。
`prepare.py` 仅供从已归档的 `source/rounds/` 重建汇总；不重新执行实验。
