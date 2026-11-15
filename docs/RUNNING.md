# 运行与查看进度

每个条件运行一次，共 52 项。配置为 `configs/experiment-suite.json`；服务地址和运行编号为 `configs/runtime.json`。

## 准备与启动

在本机仓库根目录执行：

```powershell
python infra/idcai/sync_suite.py
ssh.exe idcai-60001 /srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/direct/scripts/setup_suite.py
python infra/idcai/start_suite.py
```

`sync_suite.py` 分发运行代码；`setup_suite.py` 配置专用 Redis/TiKV 和六台采集器。准备只在开始一轮实验时执行。`start_suite.py` 启动后台 systemd 服务，按四组顺序执行，跳过已通过条件；出现错误时保存现场并退出。修复后使用同一启动命令继续。

只执行一组时使用 `python infra/idcai/start_suite.py --group 01-io`。其它组为 `02-fork`、`03-packed`、`04-agent-replay`。

小输入功能检查复用同一个执行器：

```powershell
ssh.exe idcai-60001 /srv/ax-idcai-20261113/venv/bin/python3 /srv/ax-cluster/direct/scripts/run_suite.py --check --job 01-io-01-r1
```

检查结果保存到 `checks/`，正式结果保存到对应组目录。首次启动前应完成 BrewFS、JuiceFS、TiKV 和 Packed 的实际挂载与读写检查。

## 单次检查

```powershell
ssh.exe idcai-60001 python3 /srv/ax-cluster/direct/scripts/status_suite.py
ssh.exe idcai-60001 systemctl status ax-run-002 --no-pager
ssh.exe idcai-60001 tail -n 30 /srv/ax-cluster/runs/direct/run-002/logs/suite.log
```

输出按组显示完成数：fio 16、fork 12、Packed 11、多 Agent 13。收到进度提醒后执行一次检查；服务正常运行时让后台继续。出现错误时查看对应 `failure.json`、命令输出和挂载日志，修复并重测该项。

## 目录与结果

- 协调者：A366，`idcai-60001`。
- 当前代码：`/srv/ax-cluster/direct/repo`；执行脚本：`/srv/ax-cluster/direct/scripts`。
- 二进制：`/srv/ax-cluster/direct/bin`；输入：`/srv/ax-cluster/input`。
- 当前结果：`/srv/ax-cluster/runs/direct/run-002`。
- 各条件：`<组>/<配置编号>/r1/attempt-NN`，保存配置、命令输出、指标、检查结果。
- 六台监控：各自的 `hosts/<主机>/`，每 5 秒采样，阶段边界额外采样。
- 旧结果：服务器 `run-001`；本地历史材料位于 `archive/`。

实验完成后在本机一次性取回：

```powershell
python infra/idcai/collect_direct_results.py --out results/run-002
```

该脚本先把六台监控文件汇总到 A366，再下载结果，并核对文件数量和长度。缓存、活动挂载、公共输入和凭据不进入结果包。
