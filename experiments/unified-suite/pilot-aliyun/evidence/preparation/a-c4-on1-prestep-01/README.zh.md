# A C4 On1 前置：专用采样盘与真实容量门槛

本轮不重跑 Off1，也不改原 RustFS 数据盘。原 `flat-off1/finished.json`（SHA `e02cb37a…`）仍是续跑起点；本目录只记录 On1（原 step 2）开跑前必须成立的现场证据。

## 为什么加一块盘

原 Off1 完成回执、cohort、drain 与完整冷验重算出的最短观察窗口是 **13,372 秒**，本轮取 **18,000 秒**。按原 A 池实测速率，对象盘 0 需要 **48,805,345,240 B** 观察原文，而清理后 `/srv/ax-objects-0` 只有 29,283,082,240 B 可用，即使加 20% 保留也放不下。

因此新增一块**只给观察者用**的 100 GiB PL0 云盘（`d-bp16jaiu2fcmbij8mg7i`，¥0.105/小时），挂在 objects 节点的 `/srv/ax-c4-observer-scratch`。四块 60 GiB PL1 RustFS 数据盘、Redis、Off1 回执和 code-19 工具都不动。

## 现场动作与回执

| 步骤 | 回执 | 结果 |
| --- | --- | --- |
| 购盘/挂盘/归属 | `scratch-ownership.json` | 100 GiB PL0、`delete_with_instance`、`original_disk_ids` 仍是原 5 块 |
| 首次初始化（失败） | `scratch-apply-01-failed.json` | mkfs 后 udev/lsblk 身份未就绪，校验拒绝；脚本自行 umount，未留下半挂载 |
| 空盘复原 | `scratch-remediation-01.json` | 只对这块新空盘 `rmdir` + `wipefs -a`；失败回执保留 |
| 重新初始化 | `scratch-apply-02.json`、`scratch-storage.json` | ext4 UUID `91d693ad…`，设备 64848，四个子目录同盘 |
| 清理复核 | `cleanup.json` | `outcome=not-needed`：00:18 已 apply 的四棵旧 C1 原文没有回来，也没有新的 staging |
| 原生容量 | `capacity-native.json` | code-19 原 `capacity-check.py` 采四盘、Redis、boot 与容器身份 |
| 采样盘容量 | `observer-scratch.json` | 100,056,096,768 B 总、49,295,360 B 已用，同盘四个目标 |
| 证据落盘位置 | `placement.json` | client/metadata 仍在各自 64784；objects 的 `o/archives/evidence` 在采样盘 64848 |
| 审核后容量 | `capacity.json` | `rows` 把对象观察原文与归档峰值改记到采样盘，四块 RustFS 盘只留数据增长 |
| 续跑请求 | `request.json` | step 2、空 prefix、`archive_mode=closed-observer-direct`、带 `observer_scratch` |

## 代码改动（见 `deployment.json`）

协调者新增 `code-c4-directory-hold-01`：从 code-19 全量复制，只替换/新增五个入口。原 `calibrate-c4.py`、`run-c4.py`、`check-c4-calibration.py`、`admit-c4.py`、`capacity-check.py` 的 SHA 一字未变。

- `prepare-c4-observer-scratch.py`：只认新盘 serial、整盘 100 GiB、四块原盘仍是 60 GiB ext4；apply 失败自行 umount 并写失败回执；mkfs 后等 udev 身份就绪再判定，避免把「刚格式化、lsblk 还没刷新」误判成换盘。
- `resume-c4-after-directory-hold.py`：容量/placement 认识专用采样盘（新行、同 20% 闸门），把 `observer_scratch` 传进 overlay，并把采样盘根目录纳入故障边界检查；formal 步骤因为原 wrapper 还没有同路由，直接拒绝而不是静默写回旧盘。
- `continue-c4-formal.py`：`observe_boundaries` 增加可选的采样盘根，避免 On 中途失败时看不到仍在跑的观察者。
- `calibrate-c4-closed-observer.py` / `archive-c4-closed-observer.py`：观察者的 `--out` 与归档根改到采样盘，归档仍逐成员核 SHA。

## 门槛结果

`resume-c4-after-directory-hold.py --validate-only` 通过（`passed: true`、`worker_dispatched: false`），随后在同一 15 分钟窗口内启动 On1：`ax-pilot-c4-on1-01.service`，invocation `048f667dbca245a885100a1432d97d54`。观察者实测写入 `/srv/ax-c4-observer-scratch/o/c4-a-flat-on1`（5 秒一采样），对象盘 0 只增数据增长。

本目录不含 On1 的测量结果；正式轮结束后另建目录归档。
