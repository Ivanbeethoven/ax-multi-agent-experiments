# 2026-11-11 13:44 状态

正式接受7项，全部保留在旧`c123-control-01`，未搬到新计划。旧库：7 complete、1 failed、11 pending、0 active lease。新`parallel-control-01`：0 complete、1 failed、18 pending、0 active lease。新库失败项在observe-start退出，未开始正式工作负载。

B池C3运行单元仍active，处于六轮采集开销校准。第一轮off已通过全部校验、正常卸载并exit 0；第二轮on正在copy-smallfiles。校准不计入正式完成数。最后状态采集时间为05:44:23Z（北京时间13:44:23）。

费用截至05:40:46Z（13:40:46）：新增资源估算合计**¥55.5841**，A为¥50.5210、B为¥5.0631；当前**¥9.237/小时，¥0.15395/分钟**。从12:52:46至13:40:46约48分钟增加¥7.3919。按6个真实实例ID和1块协调盘ID去重；实例报价已含各自系统盘、数据盘，不再重复计盘费。复用跳板机及原系统盘/EIP、流量和OSS费用未计入；此为按创建时间和实时报价折算，非结算账单。

`cost-a.json`、`cost-b.json`由`record-cost.py`直接生成；`cost-total.json`列出7个资源及去重结果，原始创建回执和报价的小副本在`cost-sources-a/`、`cost-sources-b/`。`state-and-c3-02.json`保留只读SQLite查询、原关闭记录SHA核对、systemd和校准阶段值，`progress.json`为简表。

本次只读取协调者数据库和少量阶段文件，没有扫描原始samples、下载大数据、启动服务、运行负载或申请lease。
