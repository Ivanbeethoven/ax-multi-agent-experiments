# 2026-10-25 单机冒烟复现命令

目标主机已经存在；不创建实例、不格式化磁盘、不清空 Redis、不删除 bucket。
所有 SSH/SCP 使用 `osdi-smoke` 别名，经既有跳板连接。不要改用公网 IP 直连。
仓库基线：`e9755b040bda2732de8afbab6b1663b3017ac6e3`。

## 1. Windows：打包和传输（源文件只读）

在仓库根目录的 PowerShell 执行。以下文件均写在本次实验目录内。

```powershell
$exp = 'experiments/cloud-smoke/2026-10-25'
$source = 'C:\Users\HXY\Documents\Codex\2026-10-03\wsl-localhost-ubuntu-24-04-home\outputs\agent-fuse-swebench\events.jsonl'
Get-FileHash -Algorithm SHA256 -LiteralPath $source
tar -czf "$exp/events.jsonl.tar.gz" -C (Split-Path $source -Parent) events.jsonl
tar -czf "$exp/fuse-replay-source.tar.gz" tools/fuse-replay/Cargo.toml tools/fuse-replay/Cargo.lock tools/fuse-replay/src tools/fuse-replay/examples tools/fuse-replay/tests
ssh osdi-smoke 'mkdir -p /data/cloud-smoke/2026-10-25/results /data/results /data/tmp'
scp "$exp/events.jsonl.tar.gz" "$exp/fuse-replay-source.tar.gz" osdi-smoke:/data/cloud-smoke/2026-10-25/
curl.exe -fL https://raw.githubusercontent.com/brewfs/brewfs/main/scripts/install_brewfs_single_node.sh -o "$exp/install_brewfs_single_node.sh"
scp "$exp/install_brewfs_single_node.sh" "$exp/install-stack.sh" "$exp/repair-rustfs-log.sh" "$exp/convert_trace.py" "$exp/instrument_latency.py" "$exp/run_smoke.py" "$exp/audit_results.py" "$exp/collect-incidents.sh" osdi-smoke:/data/cloud-smoke/2026-10-25/
```

本次源 SHA256 必须是 `4a9e1275439c36540202a980240e70751b23e95330628aed47dcbc1f26461544`；共 557,135,223 字节。
原始 trace 不随仓库重复提交。下载下来的安装器快照已保存在本目录，SHA256 见报告；将来 `main` 可能变化。

## 2. 云端：沿用官方安装器

登录后执行。`install-stack.sh` 是参数包装器，官方脚本本体未修改。
包装器将 Redis 设为 6380，BrewFS 使用 DB 0，RustFS API/console 为 9000/9001；数据、缓存、日志、二进制、配置均放 `/data`。systemd unit 仍由安装器写入 `/etc/systemd/system`。

```bash
cd /data/cloud-smoke/2026-10-25
tar xzf events.jsonl.tar.gz
tar xzf fuse-replay-source.tar.gz
# 本次将 results 目录放在 /data/results/2026-10-25，并保留原路径软链接。
# 新环境才执行以下两行；本次主机上已存在，禁止重复移动。
mv results /data/results/2026-10-25
ln -s /data/results/2026-10-25 results
bash install-stack.sh > results/install.log 2>&1
```

本次云端下载 BrewFS 超时，实际采用以下传输补救；仍通过同一个官方安装器安装，不是源码构建：

```powershell
curl.exe -fL https://download.brewfs.ai/brewfs/releases/v0.1.3/brewfs-linux-amd64 -o "$exp/brewfs-linux-amd64"
curl.exe -fL https://download.brewfs.ai/brewfs/releases/v0.1.3/brewfs-linux-amd64.sha256 -o "$exp/results/brewfs-publisher.sha256"
Get-FileHash "$exp/brewfs-linux-amd64"
scp "$exp/brewfs-linux-amd64" osdi-smoke:/data/cloud-smoke/2026-10-25/
scp "$exp/results/brewfs-publisher.sha256" osdi-smoke:/data/cloud-smoke/2026-10-25/brewfs-linux-amd64.sha256
```

```bash
cd /data/cloud-smoke/2026-10-25
# 先确保旧安装进程已经退出，且保留第一次日志；不要覆盖仍在执行的 shell 包装器。
sha256sum -c brewfs-linux-amd64.sha256
BREWFS_DOWNLOAD_URL=file:///data/cloud-smoke/2026-10-25/brewfs-linux-amd64 \
  bash install-stack.sh > results/install-staged.log 2>&1
```

首次部署 RustFS 1.0.3 时遇到 installer 生成的 stdout 与 rolling log 重名；在另一终端运行下面的局部修复，随后安装器继续完成：

```bash
bash repair-rustfs-log.sh
mountpoint /mnt/brewfs
# SSH 有 XDG_RUNTIME_DIR，systemd 服务没有：使用与服务相同的 /tmp/brewfs registry。
env -u XDG_RUNTIME_DIR /data/bin/brewfs info /mnt/brewfs
```

RustFS 来自安装器实际访问的 `https://dl.rustfs.com/artifacts/rustfs/release/rustfs-linux-x86_64-musl-latest.zip`。
该 URL 不固定版本。本次实际为 1.0.3，运行文件 SHA256 留在 environment.json；云端 `/data/bin/rustfs` 是本次可复核副本。不能把以后下载的 `latest` 自动视为本次二进制。

## 3. 构建原版回放器和独立延迟诊断副本

```bash
cd /data/cloud-smoke/2026-10-25
export PATH=/root/.cargo/bin:$PATH
export TMPDIR=/data/tmp CARGO_HOME=/data/cargo
# 本次第一次构建曾使用 /root/.cargo/registry，随后将约 20 MiB registry 移至
# /data/cargo/registry 并在原处建立软链接；后续构建显式指定 CARGO_HOME。
# /data/cargo/config.toml 沿用既有 rsproxy 配置。
export CARGO_TARGET_DIR=/data/cloud-smoke/2026-10-25/target
(cd tools/fuse-replay && cargo build --release --locked) > results/replay-build.log 2>&1
mkdir latency-probe-src
tar xzf fuse-replay-source.tar.gz -C latency-probe-src
python3 instrument_latency.py latency-probe-src/tools/fuse-replay/src/replay.rs
export CARGO_TARGET_DIR=/data/cloud-smoke/2026-10-25/target-latency
(cd latency-probe-src/tools/fuse-replay && cargo build --release --locked) > results/latency-build.log 2>&1
```

原版 `timing off` 不采样逐操作时间；原版 `timing op` 也只有总和/最大值，无法计算 p50/p99。
`instrument_latency.py` 只修改 `/data` 内的源码副本，在 `timing op` 分支保存原计时区间的样本；原版源码和主测试二进制不变。
诊断 JSON 保留全部纳秒样本，p50/p99 使用 nearest-rank。该副本新增了分配/采样/序列化开销，因此诊断吞吐不混入主测试。

## 4. 同一 Redis / RustFS 上初始化 JuiceFS

```bash
cd /data/cloud-smoke/2026-10-25
python3 run_smoke.py setup-jfs > results/setup-jfs.log 2>&1
```

脚本实际执行的关键命令如下，完整 argv/stdout/stderr/退出码在 `results/initial/command-log.jsonl` 和 `results/juicefs-setup.json`：

```bash
juicefs format --storage s3 \
  --bucket http://127.0.0.1:9000/jfs-smoke-20261025 \
  --access-key rustfsadmin --secret-key rustfsadmin \
  --compress none --trash-days 0 redis://127.0.0.1:6380/1 jfs-smoke-20261025
juicefs mount -d --no-syslog --no-usage-report --no-bgjob --backup-meta 0 \
  --cache-dir /data/jfs-cache --cache-size 8192 --buffer-size 2048 \
  --log /data/jfs-mount.log --metrics 127.0.0.1:9567 \
  redis://127.0.0.1:6380/1 /mnt/jfs
mountpoint /mnt/jfs
```

这里是 installer 的默认本机测试凭据，不是云账号密钥。已有卷不要加 `--force`、不要清库。

## 5. 统计转换与顺序采集

```bash
cd /data/cloud-smoke/2026-10-25
# 输出目录必须不存在；这是统计特征合成，不是原始事件重放。
python3 convert_trace.py --source events.jsonl --output generated --run 0 --events 100000
target/release/fuse-replay validate --trace generated/workload.jsonl
```

本次第一次四格试跑遇到宿主机后台 unattended-upgrades，全部原始数据保存在 `results/initial/`。
其负载未映射 `readdirplus`，保存在 `results/initial/workload.jsonl`；最终转换器已加入该映射。
本次将第一次 `generated` 保留为云端 `generated-initial` 后重新生成最终 `generated`。

自动更新处理与最终补跑：

```bash
systemctl is-active apt-daily.timer apt-daily-upgrade.timer
systemctl stop apt-daily.timer apt-daily-upgrade.timer
# 不停止正在运行的 apt/dpkg；本次等待日志出现 All upgrades installed，
# 且 apt-daily.service / apt-daily-upgrade.service 都 inactive。
systemctl is-active apt-daily.service apt-daily-upgrade.service
cd /data/cloud-smoke/2026-10-25
python3 run_smoke.py run --label stable > results/stable-run.log 2>&1
# 完整转换复验曾与 stable/op 诊断重叠；其数据保留，但报告延迟改用下面的独立补采。
python3 run_smoke.py run --label cleandiag --timings op > results/cleandiag-run.log 2>&1
python3 audit_results.py --results results/stable --diagnostic-results results/cleandiag --trace generated/workload.jsonl > results/stable/audit.log 2>&1
bash collect-incidents.sh
systemctl start apt-daily.timer apt-daily-upgrade.timer
systemctl is-active apt-daily.timer apt-daily-upgrade.timer
```

`run --label stable` 顺序执行 BrewFS/off、JuiceFS/off、BrewFS/op、JuiceFS/op，各用新目录。
每格先 `fuse-replay init`，建立相同的 deterministic SHAKE256 初始树并完整 fingerprint；第一格把该指纹写进唯一负载头，后续各格必须匹配。
实际主命令（两边仅 root/output 不同）：

```bash
timeout --signal=TERM --kill-after=5s 300s \
  /data/cloud-smoke/2026-10-25/target/release/fuse-replay run \
  --trace /data/cloud-smoke/2026-10-25/generated/workload.jsonl \
  --root /mnt/brewfs/cloud-smoke-20261025-stable-off \
  --output /data/results/2026-10-25/stable/brewfs-off.result.json \
  --workers 4 --pace asap --timing off --timeout-seconds 180
```

JuiceFS 的 root 为 `/mnt/jfs/cloud-smoke-20261025-stable-off`。脚本 argv 中 output 使用结果软链接路径，与 `/data/results/2026-10-25/stable/` 指向同一目录。
最终诊断使用 `target-latency/release/fuse-replay`、`--timing op`、独立 `cleandiag-op` 初始树，其他负载参数相同。

CPU/RSS 来自 `wait4` 和 100 ms `/proc` 采样；客户端 PUT 字节计数器在进程启动前和退出后 5 秒各快照一次。
不通过清系统缓存宣称冷读；初始树完整校验会暖缓存。不自动删除任何测试目录。

## 6. 归档与本地核验

```powershell
scp -r osdi-smoke:/data/results/2026-10-25/. "$exp/results/"
scp osdi-smoke:/data/cloud-smoke/2026-10-25/generated/workload.jsonl "$exp/results/workload.jsonl"
scp osdi-smoke:/data/cloud-smoke/2026-10-25/generated/synthesis.json "$exp/results/synthesis.json"
scp osdi-smoke:/data/cloud-smoke/2026-10-25/generated/trace-statistics.json "$exp/results/trace-statistics.json"
git status --short
git diff --check
powerShell.exe -NoProfile -ExecutionPolicy Bypass -File "$exp/verify_archive.ps1"
```

`verify_archive.ps1`按 `results/cloud-manifest.json`核对 129 个云端结果/输入文件、10 个脚本的哈希，再解析 JSON、核对最终 audit 和报告本地链接，写入 `results/local-verification.json`。`-ExecutionPolicy Bypass`只作用于该子进程；本次 Windows 默认禁止直接运行 ps1，没有更改持久执行策略。

最终交付的 `results/summary.json` 和 `results/stable/summary.json` 均组合 `stable/*-off` 主测试与 `cleandiag/*-op` 独立诊断；JSON 的 `raw_results_directory` 标明每行来源。
原始数据包括对应的 `*.result.json`、`*.measurement.json`、`*-backend.json` 和 `*.process-samples.json`。
`initial/` 的 audit 明确失败于服务进程中途消失检查，不能当作稳定条件下的重复样本。
重跑必须选新的 label 和结果目录；已有结果采用不覆盖策略，禁止把旧目录当作干净初始树。
