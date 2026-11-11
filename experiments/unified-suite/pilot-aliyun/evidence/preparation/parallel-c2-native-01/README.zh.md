# C2 输入路径检查

首个新计划资格检查在回放前失败。旧`inputs/cases/pytest-full-02`有112条权限与原清单不同：要求0777，实际0755。导入忠实保留了源权限，字节均一致；失败不是BrewFS丢失数据。原DB52、资产和失败记录保留，挂载及两个原进程均已正常退出。

正确输入已存在：`/srv/ax-client/trace-exact-02/cases/pytest-full-02`。原manifest为`/srv/ax-client/trace-exact-02/manifest-replay-inputs-03.json`，SHA为`71a0661b82a433e9d14da29287a2bc1860460015d4887031f90a03ff5451a021`。67,577份文件、714条初始树及初始硬链接组已只读核验通过，见`exact-source-probe.json`。

`prepare-c2.py`现先检查源树及硬链接，再创建资产和输出。重新资格须用新DB、新资产和独立执行名称；本目录不把该失败计作有效实验。
