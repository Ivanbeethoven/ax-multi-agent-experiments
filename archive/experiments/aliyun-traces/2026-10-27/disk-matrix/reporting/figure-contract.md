本组仅展示三台新实例完成同一完整 trace 的四方法单次耗时及独立 fio 校准；不将旧 e 系列结果混入，也不建立磁盘容量的单因素因果结论。

- 问题：100 GiB PL0、500 GiB PL0、500 GiB PL1 三个记录配置上，各方法完整回放分别用了多少秒。方法顺序固定为 native、native-overlay、native-overlay-pack、JuiceFS；不按耗时排序。
- 主图：quantitative grid，单面板分组水平条形图，4 个方法 × 3 个磁盘配置；横轴从 0 开始，统一秒数尺度。颜色区分磁盘配置，方法用行标签；不连线、不计算跨机加速比。
- 证据链：原 attempt 的 native／native-overlay 加隔离 retry-01 的 packed／JuiceFS；保留原端口预检失败及恢复间隔。完整回放 `execution_seconds` 是唯一主图数值，准备、fio、观测器、上传和资源生命周期均不混入。
- 硬门槛：每节点两份 archive／receipt／terminal checkpoint 的 SHA、字节数和状态吻合；四方法各 1,747,109 ops、read 281,774,624 B、write 194,706,661 B、handles 0、exit 0、无 timeout、自然 mount exit 0、cleanup 无错误。共享 combined verifier 通过。默认要求精确实例／两盘／安全组／临时桶释放证据全部为 0；root 已明确允许唯一 500 GiB PL1 节点为额外原版 overlay 验证继续保留，因此可用该项明确授权替代其释放门槛，但必须写“尚未释放”，不得冒充已清理。其他节点仍必须释放。缺一则只输出 pending，不创建图或终版 CSV。
- 来源：三节点同一 C4 archive／BrewFS binary／trace／replayer；采集真实 CPU 型号并逐节点显示。已知 8475B 与 8575C 的宿主差异、单实例 n=1、顺序执行且未清宿主页缓存，妨碍严谨的容量／PL 归因。
- 统计与正确性：每单元 n=1，无误差条、均值、统计检验或显著性。performance mode 的工作量完整不等于严格正确性，保留 `valid=false`、expected errors 与 probe errno 差异；C4 仍未通过性能验收。
- 准备成本：命令级 pack／snapshot fixture 构建上传、copy、init 等实际 wall time 单列表；若工具只记录合并阶段就不拆分。fixture 准备不代表已完成在线 freeze／fork／迁移实验。
- 校准：fio 16 GiB 预填充单列，5 个 60 秒直接 I/O case 独立表；标注 IOPS、MiB/s、clat p99 与 sync 分位的具体含义。缺失 total latency percentile 保留 null，不用 clat 代替，也不相加分位数。
- 后端与输出：沿用 Python／Matplotlib，183 mm × 118 mm，主字 8 pt、图注 7 pt、最低 5 pt；PNG 600 dpi、editable SVG、TrueType PDF、source-data.csv、紧凑中文报告及自洽证据。仅接受真实已完成数值；不渲染假图或中途数据。
- QA：源码检查；final draw 后单面板 alignment 记录；每次图形修改后 PDF 字号与碰撞 QA；检查 SVG text、CSV 数值和引用；最终尺寸视觉检查。发布前增量凭据／签名 URL 扫描。渲染需 root 最终授权，资源释放不代表性能验收。

当前状态：12 项完整回放已核验，100 GiB PL0／500 GiB PL0 已释放，500 GiB PL1 按 root 明确授权为额外原版 overlay 验证保留。矩阵图已生成，自动 QA、代理 PNG／PDF 检查和 root 实际视觉检查通过；性能仍未验收。
