# 6 节点 CephFS 与 s3fs 轻量级部署及运维交付手册

本文档记录 `idcai-60001` 至 `idcai-60555` 共 6 台服务器的 **CephFS（Ceph Squid `19.2.3`）** 与 **`s3fs`（`s3fs-fuse v1.93`）** 实际部署拓扑、轻量级配置参数、启停顺序规范及一键运维脚本用法。

---

## 1. 交付脚本清单

所有脚本已同步部署至服务器 `/usr/local/bin/`（登录跳板机即可直接执行），并在本仓库 `infra/cephfs-s3fs/scripts/` 下保留完全一致的源码副本与本地包装脚本：

| 用途 | 跳板机 / 节点内直接执行路径 | 本地仓库脚本路径 | 适用范围 |
| :--- | :--- | :--- | :--- |
| **CephFS 全集群启停/状态/挂载** | `/usr/local/bin/cephfs-cluster`（位于 `idcai-60001`） | [cephfs-cluster.sh](file:///d:/code/ax-multi-agent-experiments/infra/cephfs-s3fs/scripts/cephfs-cluster.sh) / [cephfs-cluster.ps1](file:///d:/code/ax-multi-agent-experiments/infra/cephfs-s3fs/scripts/cephfs-cluster.ps1) | 6 台节点批量编排 |
| **CephFS 单机守护进程与挂载控制** | `/usr/local/bin/cephfs-node-ctl`（全部 6 台节点） | [cephfs-node-ctl.sh](file:///d:/code/ax-multi-agent-experiments/infra/cephfs-s3fs/scripts/cephfs-node-ctl.sh) | 单台节点本机执行 |
| **s3fs 全集群配置下发/挂载/自检** | `/usr/local/bin/s3fs-cluster`（位于 `idcai-60001`） | [s3fs-cluster.sh](file:///d:/code/ax-multi-agent-experiments/infra/cephfs-s3fs/scripts/s3fs-cluster.sh) / [s3fs-cluster.ps1](file:///d:/code/ax-multi-agent-experiments/infra/cephfs-s3fs/scripts/s3fs-cluster.ps1) | 6 台节点批量编排 |
| **s3fs 单机挂载/卸载/读写自检** | `/usr/local/bin/s3fs-node-ctl`（全部 6 台节点） | [s3fs-node-ctl.sh](file:///d:/code/ax-multi-agent-experiments/infra/cephfs-s3fs/scripts/s3fs-node-ctl.sh) | 单台节点本机执行 |
| **s3fs 参数配置模板** | `/etc/s3fs/s3fs.env`（全部 6 台节点，权限 `0600`） | [s3fs.env.example](file:///d:/code/ax-multi-agent-experiments/infra/cephfs-s3fs/scripts/s3fs.env.example) | S3 地址/Bucket/AK/SK |

---

## 2. 集群拓扑、角色分工与访问归属

### 2.1 节点硬件与角色分配表

6 台服务器均运行 **Ubuntu 24.04.4 LTS（Linux `6.8.0-149-generic`）**，通过 **10 GbE 万兆内网（`eth0` `175.168.0.0/16`，RTT ~0.1 ms）** 承载全部 Ceph 通信与客户端挂载流量：

| SSH 别名 | 主机名 | 万兆内网 IP (`eth0`) | WireGuard (`axwg0`) | 配置 (CPU / 内存 / 磁盘) | Ceph 角色分配 | OSD 后备稀疏镜像 | 默认挂载点 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `idcai-60001` | `A366` | `175.168.3.66` | `10.77.13.11` | 128C / 251 GiB / 960G SSD | **跳板运维** + **MON** + **MGR(主)** + 客户端 | 无（零磁盘写入争抢） | `/mnt/cephfs` |
| `idcai-60525` | `A355` | `175.168.3.55` | `10.77.13.12` | 128C / 251 GiB / 960G SSD | **MON** + **MDS(主)** + 客户端 | 无（专职低延迟元数据） | `/mnt/cephfs` |
| `idcai-60103` | `A672` | `175.168.6.72` | `10.77.13.21` | 104C / 125 GiB / 960G SSD | **MON** + **MGR(备)** + **MDS(备)** + **OSD.0** + 客户端 | `/var/lib/ceph/osd-backing/osd-0.img` (250G) | `/mnt/cephfs` |
| `idcai-60439` | `A601` | `175.168.6.1` | `10.77.13.22` | 112C / 251 GiB / 960G SSD | **OSD.1** + 客户端 | `/var/lib/ceph/osd-backing/osd-1.img` (250G) | `/mnt/cephfs` |
| `idcai-60533` | `A635` | `175.168.6.35` | `10.77.13.23` | 104C / 251 GiB / 1T SSD | **OSD.2** + 客户端 | `/var/lib/ceph/osd-backing/osd-2.img` (250G) | `/mnt/cephfs` |
| `idcai-60555` | `A368` | `175.168.3.68` | `10.77.13.24` | 32C / 125 GiB / 960G SSD | **OSD.3** + 客户端 | `/var/lib/ceph/osd-backing/osd-3.img` (250G) | `/mnt/cephfs` |

### 2.2 跳板与内网管理密钥归属

- **跳板管理入口**：`idcai-60001`（`A366`）。
- **内网批量控制密钥**：跳板机专用密钥位于 `/etc/ceph/ops_ssh/id_ed25519`（注释标识 `cephfs-s3fs-ops@A366-20261115`），配套 SSH 配置位于 `/etc/ceph/ops_ssh/ssh_config`。
- **来源受限访问规则**：其余节点 `/root/.ssh/authorized_keys` 中该公钥均附加了 `from="175.168.3.66,10.77.13.11"` 源地址限制，仅允许从跳板机 `A366` 内网发起管理调用。

---

## 3. CephFS 核心配置说明（轻量模式）

- **版本与部署形态**：`ceph version 19.2.3 (squid)` 原生 `systemd` 服务，无 Prometheus / Grafana / Alertmanager 容器常驻开销；单节点守护进程实测常驻内存仅 **83 MiB – 566 MiB**。
- **核心配置文件**：
  - 集群配置：`/etc/ceph/ceph.conf`（FSID：`6b49f98e-2d7e-41c3-9e2b-a4eb7beb5f8b`）
  - 管理密钥：`/etc/ceph/ceph.client.admin.keyring`、内核态挂载密钥 `/etc/ceph/admin.secret`（权限均为 `0600`）
  - 开机挂载控制：`/etc/fstab` 已配置 `_netdev,noauto,noatime`；执行 `cephfs-cluster stop` 时会自动卸载并禁用守护进程自启与释放 loop 设备（确保停用期间重启机器零后台干扰），执行 `cephfs-cluster start` 时自动重新启用并挂载。
- **容量与副本参数**：
  - 4 个 SSD BlueStore OSD × `250 GiB` 稀疏文件 = **1000 GiB 裸容量**（稀疏文件按实际写入数据量占用系统盘空间）。
  - 存储池 `cephfs_metadata`（`32` PGs）与 `cephfs_data`（`64` PGs）均设为 **`size=2, min_size=1`**、故障域为 `host`，CephFS 实际可用容量 **475 GiB**，允许任意 1 台存储节点离线不丢数据、不阻塞读写。
  - 关闭后台 `pg_autoscaler`（`pg_autoscale_mode = off`），避免测试或运行期间后台自动分裂/合并 PG 产生额外 I/O 抖动。
  - 内存上限：`osd_memory_target = 2 GiB`，`mds_cache_memory_limit = 2 GiB`。
- **Loop 设备持久化单元**：4 台存储节点均安装 `/etc/systemd/system/ceph-osd-loop@.service` 与 `/usr/local/sbin/ceph-osd-loop-attach.sh`，在 `ceph-osd@<id>.service` 启动前自动查找并关联 `losetup --direct-io=on` 设备、刷新 `block` 软链接，避免重启后因 `snapd` 占用 loop 设备号导致编号漂移。

---

## 4. CephFS 启动、关闭与挂载操作指南

> [!IMPORTANT]
> **启停顺序原则**：关闭 CephFS 集群时，**必须先卸载全部客户端 `/mnt/cephfs` 挂载点，再停止 MDS / OSD / MON 服务**；若先停后端守护进程再执行 `umount`，Linux 内核态挂载会因等待 MDS 会话超时而卡住。交付的 `cephfs-cluster` 脚本已内置完整安全顺序。

### 4.1 全集群一键操作（推荐）

#### 方式一：登录跳板机 `idcai-60001` 直接执行

```bash
ssh idcai-60001

# 1. 查看全集群健康状态、MDS 状态及 6 台机器守护进程与挂载详情
cephfs-cluster status

# 2. 安全关闭整套 CephFS（自动按序：卸载6机挂载 -> 设 OSD noout -> 停主备 MDS -> 停4台 OSD -> 停 MGR/MON）
cephfs-cluster stop

# 3. 启动整套 CephFS（自动按序：启 MON/MGR -> 挂 loop 并启4台 OSD -> 取消 noout -> 启 MDS -> 挂载6机 /mnt/cephfs）
cephfs-cluster start

# 4. 重启整套集群
cephfs-cluster restart

# 5. 仅控制 6 台节点的客户端挂载（不启停后端 Ceph 服务）
cephfs-cluster umount       # 批量卸载 6 台机器的 /mnt/cephfs
cephfs-cluster mount        # 批量以 Linux 内核态（默认高性能）挂载 6 台机器的 /mnt/cephfs
cephfs-cluster mount-fuse   # 批量切换为用户态 ceph-fuse 挂载 6 台机器的 /mnt/cephfs
```

#### 方式二：在本地机器直接远程执行

- **Windows PowerShell**：
  ```powershell
  .\infra\cephfs-s3fs\scripts\cephfs-cluster.ps1 status
  .\infra\cephfs-s3fs\scripts\cephfs-cluster.ps1 stop
  .\infra\cephfs-s3fs\scripts\cephfs-cluster.ps1 start
  ```
- **Git Bash / WSL / Linux**：
  ```bash
  bash infra/cephfs-s3fs/scripts/cephfs-cluster.sh status
  bash infra/cephfs-s3fs/scripts/cephfs-cluster.sh stop
  bash infra/cephfs-s3fs/scripts/cephfs-cluster.sh start
  ```

### 4.2 单节点操作（登录任意一台机器）

每台机器均安装有 `/usr/local/bin/cephfs-node-ctl`，用于单独控制本机的挂载或本机守护进程：

```bash
# 查看本机 Ceph 服务状态与 /mnt/cephfs 挂载情况
cephfs-node-ctl status

# 仅卸载 / 重新挂载本机的 /mnt/cephfs（内核态）
cephfs-node-ctl umount
cephfs-node-ctl mount

# 将本机 /mnt/cephfs 切换为用户态 FUSE 挂载（ceph-fuse）
cephfs-node-ctl mount-fuse

# 启停本机配置的所有 Ceph 守护进程（需先卸载挂载点）
cephfs-node-ctl stop-daemons
cephfs-node-ctl start-daemons
```

---

## 5. `s3fs` 配置、挂载与卸载操作指南

全部 6 台服务器均已安装 `/usr/bin/s3fs`（`v1.93`）并启用 `/etc/fuse.conf` 的 `user_allow_other`。配置文件统一位于各节点的 `/etc/s3fs/s3fs.env`（权限 `0600`），密钥自动生成于 `/etc/s3fs/passwd-s3fs`（权限 `0600`）。

### 5.1 全集群 6 节点批量配置与挂载（推荐）

#### 方式一：在跳板机 `idcai-60001` 执行

```bash
ssh idcai-60001

# 1. 一键向 6 台机器下发 S3 连接配置（示例对接内网 RustFS / MinIO）
# 用法: s3fs-cluster configure <S3_URL> <BUCKET> <ACCESS_KEY> <SECRET_KEY> [挂载路径，默认 /mnt/s3fs]
s3fs-cluster configure http://175.168.6.72:9000 bench-bucket my_access_key my_secret_key /mnt/s3fs

# 2. 批量在 6 台机器挂载 /mnt/s3fs
s3fs-cluster mount

# 3. 查看 6 台机器的 s3fs 挂载状态
s3fs-cluster status

# 4. 在 6 台机器上分别执行创建/读回/删除冒烟自检
s3fs-cluster test

# 5. 批量卸载 6 台机器的 /mnt/s3fs
s3fs-cluster umount
```

#### 方式二：在本地机器直接远程执行

- **Windows PowerShell**：
  ```powershell
  .\infra\cephfs-s3fs\scripts\s3fs-cluster.ps1 configure "http://175.168.6.72:9000" "bench-bucket" "my_ak" "my_sk" "/mnt/s3fs"
  .\infra\cephfs-s3fs\scripts\s3fs-cluster.ps1 mount
  .\infra\cephfs-s3fs\scripts\s3fs-cluster.ps1 status
  .\infra\cephfs-s3fs\scripts\s3fs-cluster.ps1 test
  .\infra\cephfs-s3fs\scripts\s3fs-cluster.ps1 umount
  ```
- **Git Bash / WSL / Linux**：
  ```bash
  bash infra/cephfs-s3fs/scripts/s3fs-cluster.sh configure http://175.168.6.72:9000 bench-bucket my_ak my_sk /mnt/s3fs
  bash infra/cephfs-s3fs/scripts/s3fs-cluster.sh mount
  bash infra/cephfs-s3fs/scripts/s3fs-cluster.sh umount
  ```

### 5.2 单节点独立配置与挂载

若只需在单台机器（例如 `idcai-60001` 或 `idcai-60525`）挂载 `s3fs`：

```bash
# 1. 编辑本机 /etc/s3fs/s3fs.env 填入真实 S3_URL / S3_BUCKET / S3_ACCESS_KEY / S3_SECRET_KEY
vim /etc/s3fs/s3fs.env

# 2. 挂载、查看状态、读写自检、重挂、卸载
s3fs-node-ctl mount
s3fs-node-ctl status
s3fs-node-ctl test
s3fs-node-ctl remount
s3fs-node-ctl umount
```

> [!NOTE]
> 为防止误用未配置的模板，`s3fs-node-ctl` 会自动拦截包含 `REPLACE_WITH` 占位符的密钥并直接报错退出；同时针对 RustFS / MinIO 默认开启 `use_path_request_style`（路径风格请求）及 `parallel_count=16,multipart_size=16` 并发分片参数。

---

## 6. 常见运维与排障速查

1. **卸载提示 `target is busy`**：
   检查是否有终端工作目录或测试进程仍停留在挂载目录内：
   ```bash
   fuser -vm /mnt/cephfs    # 或 fuser -vm /mnt/s3fs
   ```
   退出占用进程后再执行 `cephfs-node-ctl umount` 或 `s3fs-node-ctl umount`。
2. **检查停机维护后的 `noout` 标志**：
   `cephfs-cluster stop` 会自动设置 `noout` 防止停机过程触发无意义的副本迁移；`cephfs-cluster start` 会自动执行 `ceph osd unset noout`。若手动停机恢复后看到 `HEALTH_WARN: noout flag(s) set`，执行 `ceph osd unset noout` 即可恢复 `HEALTH_OK`。
3. **存储节点物理重启后自检**：
   所有 `ceph-mon`、`ceph-mgr`、`ceph-mds`、`ceph-osd-loop`、`ceph-osd` 服务及 `/etc/fstab` 中的 `/mnt/cephfs` 均已设为开机自启；机器重启后执行 `cephfs-cluster status` 即可确认全部节点恢复 `HEALTH_OK`。
