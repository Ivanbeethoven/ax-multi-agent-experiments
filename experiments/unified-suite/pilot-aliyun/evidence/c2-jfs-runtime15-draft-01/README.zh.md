# C2 JuiceFS：接续实际 B 组运行版本

协调机上的 `refresh-c2-juicefs-draft.py` 将原 C2 草稿绑定到 C4 已部署的 code-15、原 C2 计划和原硬件观测。C2 没有另建状态库，也没有联系 B 组节点。输入仍为云内复制并逐文件核验过的 67,577 个文件、914,193,836 字节。草稿的执行开关仍为 false。

B 组完成 C4 并交接后，`continue-c2-juicefs.py` 才启动。交接必须有 SHA256 固定的关闭回执，写明实际保留的采集器名称、目录和 B 组已释放。脚本从资格检查到正式单元始终持同一物理锁，先查各阶段的状态库、空槽、空 DB80、实际文件哈希及当前分支。完整严格回放、冷重挂和容量预算通过后，才生成三对校准和一个正式单元的请求。

没有再次下载输入，没有配置模型。此目录只记录准备，不能计作正式结果。

在协调机用这两个命令启动和归档。路径用本次实际交接回执替换；不要自行填一个“已释放”的回执。

```bash
root=/srv/ax-pilot-control
control=$root/parallel-control-01
code=$root/code-15
tools=$code/experiments/unified-suite/pilot-aliyun/scripts
recipe=$control/drafts/pool-b-c2-runtime15-01/recipe/draft.json
handoff=/path/to/actual-c4-closure-handoff.json
unit=ax-pilot-b-c2-jfs-runtime15-01
systemd-run --unit "$unit" --collect --property=CPUAccounting=yes \
  --property=MemoryAccounting=yes --property=IOAccounting=yes \
  /usr/bin/python3 "$root/preparation/c2-juicefs-runtime15-tools-01/continue-c2-juicefs-03.py" \
  --runtime-repo "$code" --recipe "$recipe" \
  --recipe-sha256 "$(sha256sum "$recipe" | cut -d ' ' -f 1)" \
  --handoff "$handoff" --handoff-sha256 "$(sha256sum "$handoff" | cut -d ' ' -f 1)" \
  --out "$control/launches/pool-b-juicefs-c2-01"
```

只在正式结果关闭且进程退出后归档：

```bash
request=$control/requests/pool-b-juicefs-c2-01.json
finished=$control/one-cell-runs/05357b5089cd679c-r1-juicefs-c201/result.json
nice -n 15 ionice -c 3 python3 "$tools/retain-calibrations.py" \
  --unit "$unit" --request "$request" \
  --request-sha256 "$(sha256sum "$request" | cut -d ' ' -f 1)" \
  --completed-sha256 "$(sha256sum "$finished" | cut -d ' ' -f 1)" \
  --out "$control/retained-calibrations/pool-b-juicefs-c2-01"
```
