# A 池重启与再停机（2026-11-12 09:20）

为验证 C4 跨机恢复的前置，A 三台在 09:10 左右启动、09:17 前完成服务恢复、09:20 再次 `StopCharging`。这次没有跑任何计时轮，也没有产生正式样本。

## 实际恢复的东西（`scripts/a-pool-up.py`，新增）

| 角色 | 动作 | 结果 |
| --- | --- | --- |
| objects | 按 serial 挂 4 块 60 GiB（`6kvd/6kve/6kvf/6kvg`）到 `/srv/ax-objects-{0..3}` | 全部 ext4、UUID 未变 |
| objects | 重建 `/srv/ax-rustfs-data-{0..3}` bind | 四个 bind 全部就位 |
| objects | 挂采样盘 `bp16jaiu2fcmbij8mg7i` 到 `/srv/ax-c4-observer-scratch` | UUID `91d693ad…` 与原 storage.json 一致，22.47 GB On1 观察原文仍在 |
| objects | `docker start ax-pilot-20261111-objects-01-rustfs-1` | 容器 Running |
| metadata | 挂 60 GiB 到 `/srv/ax-metadata` | 原 Redis 数据目录在盘上 |
| metadata | 停镜像自带 `redis-server.service`，起 `ax-pilot-redis.service` | `CONFIG GET` 核实：`appendonly yes`、`databases 256`、`dir /srv/ax-metadata/redis`、`bind 127.0.0.1 10.20.2.162` |
| client | 挂 100 GiB 到 `/srv/ax-client` | `code-19`、`assets/bin/{juicefs,opencode}` 都在，slot 为空 |

关键经验：**三台的数据盘重启后都不会自动挂载**，client 也一样；镜像自带 `redis-server.service` 会抢占 `127.0.0.1:6379`，必须显式停掉才能让原 `ax-pilot-redis` 绑上 loopback。

## 跨机恢复仍然缺什么（按执行顺序）

1. **共享工作区不存在**：`/srv/ax-pilot-session` 在 A client 上没有创建。需要在 A 的 RustFS 建一个 session bucket、在 A Redis 选空闲库（20–255，现场 `DBSIZE=0`）、`juicefs format` 后同时挂到 A client 与 B client——这才是两端「同一个 anchor」。
2. **checkpoint 未装入**：验证过的 checkpoint 仍在协调者 `/srv/ax-pilot-control/preparation/c4-state-reopen-draft-01/verified-checkpoint`，还没有按 `prepare-session-input.py` 的 `tree/sync_tree` 装进共享工作区。
3. **五份 cohort 与 spec 未生成**：`bench.runtime.handoff` 要求 quiesce/lease_check/restore/resume/export_after 五个 `experiment-cohort-v1`，各自一个 1-agent worker 且 peer 必须与 source/target 完全一致；目前只有 draft。
4. **独立 relay 未起**：旧 18443 服务不动，新端口 + 私有配置 + 新 key 还没准备。
5. **B client 未启动**：目标端 peer、mount、binary pin 都要在它开机后现场核。

这些合计约 4–6 小时工程量，且需要 A+B 同时在线（约 ¥6/小时），所以本轮在完成「A 恢复 + 前置核实」后即停机，没有把池子挂在空转上。
