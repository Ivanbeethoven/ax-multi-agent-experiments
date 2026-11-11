# A / B 池停机后的重启前置（2026-11-12 06:20 收尾）

A 三台与 B 三台现均为 `Stopped / StopCharging`；新增的 100 GiB PL0 采样盘 `d-bp16jaiu2fcmbij8mg7i` 仍挂在 A objects 上（`In_use`），四块 60 GiB PL1 数据盘与原系统盘未动。原跳板 `i-bp1cp8ccqor34u2bo5os` 保持运行。

## 为什么停

On1 的测量与冷验已完成，但归档被冻结的 16 MiB 清单上限挡住（见 [a-c4-flat-on1-archive-bound-01](../../calibration/a-c4-flat-on1-archive-bound-01/README.zh.md)）。下一步要先决定「清单上限」还是「拆包」，在做出决定前 A 池没有任何可执行步骤，继续运行只是空转（约 ¥4.67/小时）。

## 重启 A 池必须按序做的事

1. **boot identity 会变**：`/proc/sys/kernel/random/boot_id` 变了以后，原来的 `capacity.json` / `placement.json` / `observer-scratch.json` 全部失效，必须重做「清理复核 → 原生容量快照 → 采样盘快照 → placement → 新容量文件 → 新请求」，不能沿用旧文件。
2. **四块 RustFS 盘不会自动挂载**：按原 serial/UUID 重挂 `/srv/ax-objects-{0..3}`，再重建 `/srv/ax-rustfs-data-{0..3}` 的 bind，最后 `docker start` 原容器；不得 mkfs。
3. **采样盘也不会自动挂载**：`/srv/ax-c4-observer-scratch` 目录会存在但未挂载。`prepare-c4-observer-scratch.py` 只处理「全新空盘」，因此重启后需要一条等价的只读挂载+身份复核步骤（新工具，不能复用 `--apply`）。盘上现有 22.47 GB On1 观察原文与 379.9 MB 失败归档，必须保留。
4. **Redis（A metadata）**：`ax-pilot-redis` 系列服务需要重新拉起并按原 conf 校验绑定与库数；B 池已证明「镜像自带 6379 实例抢占 loopback」会让原实例起不来，A 侧重启后必须核 `CONFIG GET bind/databases/appendendonly` 的实际值。
5. **租约**：五个状态库的 `cell_leases` 目前为空；重启后任何新一轮仍要走 `resource_guard`，不能因为「昨天是我在跑」就跳过。

## 已知仍需人工决定的两件事

- C4 On1 的归档格式（提高清单上限 vs 观察原文单独发布），决定了下一轮能否真正产出 `finished.json`。
- B 池 C1 的元数据前提：原 Redis 未在服务（`10.20.2.169:6379` 拒绝连接），只能先做一次明确的宿主修复窗口。
