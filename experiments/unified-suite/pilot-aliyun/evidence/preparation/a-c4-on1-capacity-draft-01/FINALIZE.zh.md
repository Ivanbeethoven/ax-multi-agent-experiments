# 由主协调者完成的后续步骤

本目录目前不能执行准入。对象盘 0 的真实空间需先满足审核后的完整观察原文、单轮增长、归档峰值与 20% 字节/inode 保留量。不能缩短到原 Off1 13,372 秒下界以下，也不能把省掉的第二份 staging 当成观察原文消失。

1. 在真实 scratch 清理 apply 完成后，生成新的 `pilot-prestep-cleanup-v1` 回执：绑定原请求、pool-a、step 2、原 Off1 `finished.json` prefix；`outcome=cleaned` 并列出实际 `passed=true`、`applied=true`、回收组非空的 reclaimer `result.json` pins。`finished_at` 使用真实完成时间。
2. 部署计划中的新协调者代码树时，从 `/srv/ax-pilot-control/code-19` 复制到不存在的 `/srv/ax-pilot-control/code-c4-directory-hold-01`，仅覆盖 `code-overlay/` 的五个文件并核对 `deployment-plan.json` 的 SHA。保持原请求中的 `calibrate-c4.py`、`run-c4.py`、`check-c4-calibration.py`、`admit-c4.py` 和其他工具指向原 code-19；三个 worker 的七个文件只核 SHA，不改动。
3. 在上述清理及部署之后、空闲边界内，用原容量工具采集新快照。新输出父目录须先存在，快照文件必须不存在。下面命令只展示确定参数，实际新输出路径由主协调者选择：

```bash
python3 -B /srv/ax-pilot-control/code-19/experiments/unified-suite/pilot-aliyun/scripts/capacity-check.py snapshot \
  --control /srv/ax-pilot-control/fork-control-01 --pool pool-a \
  --objects-container d6c8829b0026ca782efa53629e3bd9ffe8e5b9b23404b079cf35921b7b94cc6f \
  --runtime-control /srv/ax-pilot-control/fork-control-01/runtime-a-03/binding.json \
  --runtime-control-sha256 224d7be366ee28d87e8c185190a30c644672def5ef8ff6edb5f25c4772d2ed41 \
  --before /srv/ax-pilot-control/fork-control-01/batch-a-flat-03/capacity-after.json \
  --out "$fresh_snapshot"
```

4. 清理后重新读取每个 worker 的 `o`、`archives`、`evidence` 原生 `st_dev` 和 boot ID，保存 `c4-archive-device-placement-v1`。当前观察到 client/metadata/objects 三类目录均为其第一盘设备 64784；此处的旧观测不替代新 placement。协调者计划 `out=/srv/ax-pilot-control/fork-control-01/continuation-on1-01`，其 parent、原 `flat-on1`、`archives`、`raw` 的目标设备也须实际再核。
5. 把真实 cleanup、snapshot、placement pins 加入容量文件和去重 `source_pins`。保留原 before/after、原 Off1 四个时长来源、每角色两份 observer series 和对应 archive-check。按新快照重新核全部原生盘、inode、Redis 内存/AOF及协调者预算；只有全通过才生成独立的 `passed=true`、`reviewed=true` 最终容量文件。当前草案不应直接改名当作通过回执。
6. 生成独立最终续跑请求，填最终容量 pin、已部署代码的实际 pins，保持原 step 2 与空 prefix。先执行原续跑入口 `--validate-only`；它会持原物理锁、重新核原终态/租约/完整 Off1/空停点/容量，且不派发 worker。通过后，主协调者在时效内再次运行同一入口执行 On1；入口会重新检查全部门槛。任何失败保留新目录，不能重放已有 attempt。

```bash
python3 -B /srv/ax-pilot-control/code-c4-directory-hold-01/experiments/unified-suite/pilot-aliyun/scripts/resume-c4-after-directory-hold.py \
  --request "$reviewed_request" --sha256 "$request_sha256" --validate-only
```

每轮都另生成清理边界与新快照，不能把 On1 容量重复用于 On2 或以后轮次。On1 仍是原 `c4-a-flat-on1` 和原 collector chain；此准备不会启动正式 C4。
