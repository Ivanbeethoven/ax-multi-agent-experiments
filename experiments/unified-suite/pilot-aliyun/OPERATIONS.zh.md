# 运维速查：2026-11-11/12 这轮踩过的环境坑

**适用范围**：本批 = `experiments/unified-suite/pilot-aliyun` 的冻结协议（`code-19` 及其 overlay 目录）。三条底线：**只读优先**（诊断默认不写盘、不删数据，`gc --delete` 只在 reviewed 决定后执行）；**失败证据不重跑**（`attempts/failed-NN.json`、`failed.json`、`*-failed.json` 是证据不是待办，重试一律新开目录、递增编号）；**不改冻结代码**（改动只以 overlay 目录出现，如 `code-c4-directory-hold-01`，冻结的 `bench/archive_check.py`、`capacity-check.py`、`admit-c4.py` 一字不动）。

下面每条都是**已经查明并解决**的：症状 → 根因 → 可直接复制的命令 → 证据落点。命令在对应主机以 `root` 执行；跨机执行一律先传脚本再跑，不要用 `ssh … python3 -c "<多行>"`（坑 6）。

## 坑 1 实例停止后再启动，A/B 两块池的所有数据盘都不自动挂载

### 症状
开机后 `df -h` 只有系统盘；`docker ps` 显示 RustFS 容器 Running 但 `/data/rustfs*` 是空的；`ax-pilot-redis` 起不来并报数据目录缺失；A client 上 `/srv/ax-client/code-19`、`assets/bin/juicefs` 一并消失。

### 根因
数据盘随实例 `StopCharging` 脱离、`StartInstance` 后由云助手/init 重新挂载，而本批盘表没有写进 `/etc/fstab`，所以**四块 RustFS 盘、metadata 盘、client 盘全部需要显式重挂**；RustFS 的四个 bind、Docker 容器身份、Redis 数据目录都建立在这些挂载之上，缺一个就整链失败。B 池同样（四盘 `bp1bhdngko7nq3c2i340…i343`、metadata `bp14nenf7osz9v1rkk5l`）。

### 处置（命令）
```bash
# 只读核对：先确认盘在位、serial 与 UUID 未变（不要凭 lsblk 的 uuid 判新盘，见坑 5）
lsblk --json --bytes --paths --output NAME,TYPE,SERIAL,SIZE,UUID,FSTYPE,MOUNTPOINTS

# A 池三角色（先把 scripts/a-pool-up.py 传到该机 /root/ 再跑；--out 必须全新，脚本核 hostname）
python3 /root/a-pool-up.py objects  --out /srv/ax-receipts/a-pool-up-$(date -u +%Y%m%dT%H%M%SZ)-objects
python3 /root/a-pool-up.py metadata --out /srv/ax-receipts/a-pool-up-$(date -u +%Y%m%dT%H%M%SZ)-metadata
python3 /root/a-pool-up.py client   --out /srv/ax-receipts/a-pool-up-$(date -u +%Y%m%dT%H%M%SZ)-client
```
`a-pool-up.py` 按 serial 找到整盘、要求 `fstype=ext4` 且有 UUID，才 `mount -t ext4 -o rw,noatime`；随后重建 `/srv/ax-rustfs-data-{0..3}` 的 bind、挂采样盘、`docker start ax-pilot-20261111-objects-01-rustfs-1`。**永远不对原盘 mkfs**。

### 证据
`evidence/preparation/a-pool-restart-20261112-0910/README.zh.md`（三角色逐行动作表：4×60 GiB `6kvd/6kve/6kvf/6kvg`、采样盘 UUID `91d693ad…`、`code-19`/`assets/bin` 回位）；脚本 [`a-pool-up.py:68`](scripts/a-pool-up.py#L68)（`mount_disk`）、[`:82`](scripts/a-pool-up.py#L82)（`bring_objects`）。B 池重挂两次跑通的记录在 `evidence/preparation/b-c1-gc-maintenance-01/README.zh.md`（第 14 次尝试）。

## 坑 2 镜像自带 redis 抢占 127.0.0.1:6379，原 unit 永远 `bind: Address already in use`

### 症状
`ax-pilot-redis.service` 启动失败、`journalctl -u ax-pilot-redis` 报 `bind: Address already in use`；但 `redis-cli -h 127.0.0.1 PING` 有 `PONG`，`INFO persistence` 却是 `aof_enabled:0`、`dir /var/lib/redis`、只有 16 个库（`SELECT 61` 报 `ERR DB index is out of range`），而私网地址 `10.20.2.169:6379` 直接 `Connection refused`。

### 根因
镜像自带一个 `redis-server 127.0.0.1:6379`，在原 unit 之前就占了 loopback。原 unit 的 conf 同时 bind `127.0.0.1` 与私网地址，因此只有 loopback 被占也起不来。`kill` 进程无效时是被 systemd 拉起（A metadata 有 `redis-server.service`，`a-pool-up.py` 先 `systemctl stop` 才是对的）；B 的 02:00 只读诊断里 `systemctl list-units --type=service --all` 又看不到任何 redis* unit，22:53 的成功窗口却记录 `stopped_unit: redis-server`。**两处证据不一致，所以处置不能假设 unit 一定在或一定不在**：先试停 unit，再按 argv 杀非同配置进程，最后轮询端口释放。

### 处置（命令）
```bash
# 1) 先停镜像 unit（不存在也不要紧）；A conf=/etc/redis/ax-pilot-20261111.conf（bind 127.0.0.1 10.20.2.162）
#    B conf=/etc/redis/ax-pilot-20261111-b.conf（bind 127.0.0.1 10.20.2.169）
CONF=/etc/redis/ax-pilot-20261111-b.conf; IP=10.20.2.169
systemctl stop redis-server.service redis.service 2>/dev/null

# 2) 杀掉 argv 不等于本 conf 的 redis-server（只杀外来者）
python3 - "$CONF" <<'PY'
import os,sys,pathlib
conf=sys.argv[1]
for e in pathlib.Path('/proc').iterdir():
    if not e.name.isdigit(): continue
    try: argv=[p.decode() for p in (e/'cmdline').read_bytes().split(b'\0') if p]
    except OSError: continue
    if argv and pathlib.Path(argv[0]).name=='redis-server' and argv[1:]!=[conf]:
        print('kill', e.name, argv); os.kill(int(e.name), 15)
PY

# 3) 等端口真空，再起原 unit
for i in $(seq 30); do ss -H -ltn | grep -q ':6379' || break; sleep 1; done
systemctl start ax-pilot-redis.service

# 4) 核「生效值」，不看配置文件、不看命令行；期望 yes / 256 / /srv/ax-metadata/redis / 127.0.0.1 <私网 IP>
for k in appendonly databases dir bind; do redis-cli -h $IP CONFIG GET $k; done
```
镜像的 init 下次开机还会再拉起它，所以**每个窗口都要重做一遍**；想一次根治要另一次受审核的宿主改动（disable 该 init 或从镜像层去掉）。

### 证据
`evidence/preparation/a-pool-restart-20261112-0910/README.zh.md`（A metadata 停 unit→起原 unit→`CONFIG GET` 四件套）；`evidence/preparation/b-c1-gc-maintenance-01/metadata-redis-diagnosis-02.json`（`ss` 里 `127.0.0.1:6379 users:(("redis-server",pid=935,fd=6))`、`aof_enabled:0`、私网地址 refused、`units.out` 为空）；`evidence/preparation/b-c1-gc-maintenance-01/gc-18/node-metadata.jsonl`（`redis_preparation.actions` = 停 `redis-server`、起 `ax-pilot-redis.service`，`effective` 四件套一致）；脚本 [`a-pool-up.py:129`](scripts/a-pool-up.py#L129)、[`b-c1-gc-node.py:260`](scripts/b-c1-gc-node.py#L260)。

## 坑 3 `ss` 的行尾不是 `:6379`，`endswith(':6379')` 永远判假

### 症状
节点脚本报告「端口没被占用」，于是从不去释放外来 redis，原 unit 每次启动都 `bind: Address already in use`；连续多个维护窗口（06、09、10、12、14）卡在 metadata 侧。05 次是反例：当时用 `ss -H -ltn sport = :6379` 正确看到了监听，但选择「拒绝接管」，同样跑不下去。

### 根因
`ss -H -ltn` 每行以 `users:(("redis-server",pid=935,fd=6))` 结尾，`:6379` 出现在行中间。用行尾匹配判监听恒为假，于是「先释放外来实例」的分支永远不执行。

### 处置（命令）
```bash
# 正确：整行子串匹配
ss -H -ltn | grep -q ':6379' && echo LISTENING || echo FREE
# 要看是谁在听，加 -p（仍然不要用行尾匹配）
ss -H -ltnp | grep ':6379'
```
代码里就这一行，别写成 `endswith`：
```python
def redis_listening():
    # ss 行以 users:(...) 结尾，只按地址:端口子串匹配
    return any(':6379' in row for row in get('ss', '-H', '-ltn').splitlines())
```

### 证据
`evidence/preparation/b-c1-gc-maintenance-01/README.zh.md`（「根因是 `redis_listening()` 用 `ss` 行尾匹配…改成按地址端口子串匹配后一次通过」）；`evidence/preparation/b-c1-gc-maintenance-01/attempts/b-c1-gc-05-node-metadata.stderr.log`（`sport = :6379` 版本直接拒绝）；[`b-c1-gc-node.py:170`](scripts/b-c1-gc-node.py#L170)、[`a-pool-up.py:119`](scripts/a-pool-up.py#L119)。

## 坑 4 `INFO persistence` 没有 `aof_current_size`，是「原 AOF 没在服务」而不是版本差异

### 症状
`redis-cli -h 10.20.2.169 INFO persistence` 拿不到 `aof_current_size`，节点脚本直接抛 `Redis persistence info lacks aof_current_size`（09、10 两次）；改用「appendonly 目录实测大小」后又报 `aof_enabled = 0`（12 次）。

### 根因
当时读到的是镜像自带、`appendonly no` 的那个实例，它本来就没有 AOF 计数。**原 unit 正常服务时字段是存在的**：18 次实跑 `aof_current_size: 232092056`、`aof_current_size_source: "info"`。所以「缺字段」是诊断信号——说明原 AOF 还没加载或根本没连对实例——先证明生效配置，再谈计量。

### 处置（命令）
```bash
# 1) 先在私网地址上证明「是原实例」：appendonly=yes / databases=256 / dir=/srv/ax-metadata/redis
redis-cli -h 10.20.2.169 CONFIG GET appendonly
# 2) 再取计数；info 缺字段时才回落到磁盘实测
redis-cli -h 10.20.2.169 INFO persistence | grep -E 'aof_enabled|aof_current_size|aof_last_write_status'
du -sb /srv/ax-metadata/redis/appendonlydir 2>/dev/null || du -sb /srv/ax-metadata/redis
```
脚本里的顺序是「INFO 优先、字段缺席则用 appendonly 目录累加」，并把来源写进回执（`aof_current_size_source`），不要用磁盘大小冒充 INFO 值。

### 证据
`evidence/preparation/b-c1-gc-maintenance-01/gc-18/result.json`（`aof_current_size: 232092056`、`source: "info"`）、`gc-18/node-metadata.jsonl`（`aof_enabled:1`）；反例 `metadata-redis-diagnosis-02.json`（`aof_enabled:0` 且无该字段）；[`b-c1-gc-node.py:487`](scripts/b-c1-gc-node.py#L487)（`aof_files_bytes`）、[`:496`](scripts/b-c1-gc-node.py#L496)（`redis_aof` 的回落逻辑）。

## 坑 5 mkfs 之后 udev/lsblk 身份有竞态，会把「刚格式化」误判成「换盘」

### 症状
新采样盘第一次 apply 失败：`Scratch mount/UUID/options differ from its whole device`，回执里 `uuid: null`、`device: null`——但盘其实已经格式化成功、也挂上了。

### 根因
`mkfs.ext4` 返回时内核已有 UUID，udev 还没把 `lsblk` 的记录刷新过来，读到的是 `uuid: null` 的旧行。用 `lsblk` 判「新盘身份」就会把正常格式化当成盘不对。

### 处置（命令）
```bash
# 正确顺序：直读块设备 → settle → 轮询，直到 blkid 与 lsblk 都说 ext4 且 UUID 一致
mkfs.ext4 -q -m 0 -i 4096 -L ax-c4-observer /dev/vdf
udevadm settle
blkid -p -o export /dev/vdf            # TYPE=ext4 / UUID=... 直读，不看缓存
for i in $(seq 30); do blkid -p -o export /dev/vdf | grep -q '^UUID=' && break; sleep 0.5; done
mount -t ext4 -o rw,noatime,nosuid,nodev /dev/vdf /srv/ax-c4-observer-scratch
```
判定用「`blkid` 直读的 UUID」与「`findmnt` 报的 UUID/设备号」比对；`lsblk` 只用于看拓扑。失败时脚本自己 `umount`，不留半挂载。**复原只对从未用过的空盘**：`rmdir` 空挂载点 + `wipefs -a`，并且把失败回执留着。

### 证据
`evidence/preparation/a-c4-on1-prestep-01/scratch-apply-01-failed.json`（`uuid:null`、`device:null`、同一条错误文本、`unmounted:true`）；`…/scratch-remediation-01.json`（`rmdir` + `wipefs -a` + `udevadm settle`，`blkid` 确认为空）；`…/scratch-apply-02.json`（8 秒后成功，UUID `91d693ad-0c15-4e6e-b843-73e581aec594`、设备 64848）；[`prepare-c4-observer-scratch.py:176`](scripts/prepare-c4-observer-scratch.py#L176)（`device_uuid`）、[`:183`](scripts/prepare-c4-observer-scratch.py#L183)（`wait_identity`）。

## 坑 6 guard 脚本不能用 `ssh … python3 -c "<多行>"`；被拒绝的 guard 必须主动释放

### 症状
01 次尝试「未启动实例」即失败；07、11 次更贵：上一次失败的 guard 进程还活着，`runner-locks` 里的物理锁被占，整个窗口作废（`Another preparation or cell owns physical resource`）。

### 根因
一是 `ssh host python3 -c "<多行>"` 的多行脚本会被 ssh 重组参数（引号/换行在远端重新分词），脚本根本没按预期跑；二是 guard 在 stdin 上等一行 `release`，任何失败路径不写 `release` 就直接退出，锁就永远留在 flock 上。

### 处置（命令）
```bash
# 上传脚本（umask 077 + cat 重定向），再执行——永远不要把多行塞进 -c
ssh -T -F /srv/ax-pilot-control/fork-control-01/runtime-15-01/ssh.conf ax-pilot-jump \
  'sh -c "umask 077; cat > /tmp/guard-$$.py"' < guard.py
ssh -T -F /srv/ax-pilot-control/fork-control-01/runtime-15-01/ssh.conf ax-pilot-jump \
  python3 -B /tmp/guard-$$.py     # 第一行必须回 held，收尾必须回 release

# 事故后先看锁有没有残留（只读）
ls -l /srv/ax-pilot-control/parallel-control-01/runner-locks
fuser -v /srv/ax-pilot-control/parallel-control-01/runner-locks/*.lock 2>/dev/null
```
协调者侧的正确写法是：上传 → `python3 -B` → 读首行必须是 `held`；读不到就 `release()`（写 `release\n` → 关 stdin → `wait(60)`）后再抛错；正常路径 `require(release()==0)`。

### 证据
`evidence/preparation/b-c1-gc-maintenance-01/README.zh.md` 尝试表 01（`-c` 被拆参）、07/11（上一次 guard 未释放、物理锁被占）；[`b-c1-gc-apply.py:227`](scripts/b-c1-gc-apply.py#L227)（上传脚本）、[`:246`](scripts/b-c1-gc-apply.py#L246)（拒绝也要 release）、[`:184`](scripts/b-c1-gc-apply.py#L184)（guard 正文，末行等 `release`）。

## 坑 7 节点协议感知不到子进程死亡，已死的会话会把物理锁占到 deadline

### 症状
节点进程早就退出了，协调者仍在 `wait('closed')` 上干等，实测一直等到 1 小时的协议 deadline；期间物理锁和两台实例都被白占，成本与窗口一起浪费。

### 根因
读循环只等「下一行」，不查子进程状态。ssh 子进程死掉后队列里再也不会来新行，唯一的出口就是 deadline；EOF 也只是一行 `None`，与「暂时没数据」无法区分。

### 处置（命令）
```python
def row(self, deadline):
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Node protocol deadline expired: ' + self.alias)
        try:
            line = self.rows.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            # 进程已经退出且队列为空：立刻报错，不要占着锁等到 deadline
            require(self.process.poll() is None or not self.rows.empty(),
                    'Node process exited before %s reported it; inspect %s'
                    % (self.alias, self.error_path))
            continue
        require(line is not None, 'Node closed the protocol unexpectedly: ' + self.alias)
        return json.loads(line)
```
配套两条：节点侧收尾 `close` 之外加 `--eof-grace`（默认 60 秒，防协调者 stdin 掉了就把盘卸掉）；协调者侧 `kill()` 要幂等（关 stdin → `kill` → `wait(60)`），错误信息里带 stderr 路径。

### 证据
`evidence/preparation/b-c1-gc-maintenance-01/README.zh.md`（「第 09 次之后…`NodeSession.wait` 仍等到 1 小时 deadline…已改为『进程退出且队列为空即立即报错』」）；[`b-c1-gc-apply.py:386`](scripts/b-c1-gc-apply.py#L386)；[`b-c1-gc-node.py:14`](scripts/b-c1-gc-node.py#L14)（EOF grace）。

## 坑 8 冻结归档清单有 16 MiB 上限，5 小时全采样必然越界

### 症状
C4 On1（step 2）观察完整跑完，归档阶段却报 `ValueError: unbounded internal manifest`，整轮没有 `finished.json`，不能计入正式通过项；失败点在 objects 角色，client/metadata 的归档根本没轮到。

### 根因
`bench/archive_check.py:37` 对内部清单 `.suite-manifest.json` 设了 `member.size > 16*1024**2` 即拒绝。5 小时窗口、5 秒一采样把观察原文写成 132,939 个文件、22,469,160,960 B，单清单 20,430,785 B（19.5 MiB）、列 132,944 个成员——**越界是窗口规模的必然结果，与负载无关**。缩短窗口会违反 Off1 的 13,372 秒下界，不可取。

### 处置（命令）
```bash
# 拆包：观察原文单独 tar + 流式 NDJSON 索引；闭合/其他证据仍走冻结 pack/verify
python3 /srv/ax-objects-0/code-19/experiments/unified-suite/pilot-aliyun/scripts/pack-c4-observer-split.py \
  --root /srv/ax-c4-observer-scratch --tag c4-a-flat-on1 --role objects \
  --cell-id c5e97774bcffa69a-r1-native --include q/c4-a-flat-on1-closure \
  --out-dir /var/tmp/c4-split-01 --code /srv/ax-objects-0/code-19
# 产物与回执随后搬到采样盘，SHA256 不变
mv /var/tmp/c4-split-01 /srv/ax-c4-observer-scratch/archives-split-01
```
实测规模：132,939 个成员、观察原文包 379,794,692 B（SHA256 `504e8450…`）、闭合包 552 B / 1 成员、墙钟 6:51.95、峰值内存 269,316 kB、exit 0 且两包都重读校验。**冻结校验器一分没改**，只是把大块原文挪出了它的单清单模型。

### 证据
`evidence/calibration/a-c4-flat-on1-archive-bound-01/README.zh.md`（20,430,785 B / 132,944 成员 / `archive_check.py:37` 原文）、`…/archive-stderr.txt`（完整 traceback）；`evidence/preparation/c4-observer-split-01/README.zh.md` 与 `…/captured-stdout.txt`（上表全部数字）；[`pack-c4-observer-split.py:76`](scripts/pack-c4-observer-split.py#L76)、[`:104`](scripts/pack-c4-observer-split.py#L104)。

## 坑 9 准入证据有 15 分钟时效窗，顺序还必须对

### 症状
`resume-c4-… --validate-only` 拒：`snapshot/placement/capacity is older than 900 seconds`，或 `capacity must not precede the cleanup check`。手工分五步取材，做到最后一步时第一件已经过期。

### 根因
冻结准入要求五件证据都落在开跑前 900 秒内，且顺序固定 **清理回执 → 原生容量快照 → 采样盘快照 → placement → 审后容量 → 续跑请求**。任何一步人工停顿、任何一次「先做容量再回头清点」都会把窗口撑破。

### 处置（命令）
协调者侧一条流水线跑完（脚本内部按顺序写文件，最后 `check_order` 用 `cleanup.finished_at` 与 900 秒核对）：
```bash
python3 /srv/ax-pilot-control/code-c4-directory-hold-01/experiments/unified-suite/pilot-aliyun/scripts/c4-on1-preflight.py \
  --control /srv/ax-pilot-control/fork-control-01 \
  --runtime /srv/ax-pilot-control/fork-control-01/runtime-15-01 \
  --code /srv/ax-pilot-control/code-c4-directory-hold-01 \
  --draft /srv/ax-pilot-control/fork-control-01/preparation/c4-on1-continuation-03/draft/capacity-draft.json \
  --request-draft /srv/ax-pilot-control/fork-control-01/preparation/c4-on1-continuation-03/draft/request-draft.json \
  --reclaim /srv/ax-pilot-control/fork-control-01/preparation/c4-a-reclaim-01/result.json \
  --remediation /srv/ax-pilot-control/fork-control-01/preparation/c4-on1-scratch-01/remediation.json \
  --ownership /srv/ax-pilot-control/fork-control-01/preparation/c4-on1-scratch-01/ownership.json \
  --ownership-sha256 "$OWNERSHIP_SHA256" \
  --scratch-remote-dir /srv/ax-receipts/c4-on1-scratch/prepare-02 \
  --records /srv/ax-pilot-control/fork-control-01/preparation/c4-on1-prestep-02 \
  --out /srv/ax-pilot-control/fork-control-01/continuation-on1-02 \
  --cell c5e97774bcffa69a-r1-native --tag c4-a-flat-on1
```
`--records`、`--out`、`--scratch-remote-dir` 必须每轮新建（脚本对已存在目录直接拒绝）；`--draft/--request-draft` 用当轮 reviewed 草稿。跑完立刻在**同一窗口**内启动 unit，过期就得整链重来。

### 证据
`evidence/preparation/a-c4-on1-prestep-01/README.zh.md`（五件回执的生成顺序表；「随后在同一 15 分钟窗口内启动 On1」）；[`c4-on1-preflight.py:26`](scripts/c4-on1-preflight.py#L26)（`FRESH_SECONDS = 900`）、[`:64`](scripts/c4-on1-preflight.py#L64)（`check_order`）、[`:312`](scripts/c4-on1-preflight.py#L312)（写完容量后当场校验）；上一轮五件回执实物在 `evidence/preparation/a-c4-on1-prestep-01/`。

## 坑 10 `gc --delete` 的回收量远小于 pending delete

### 症状
只读轮报 20,101 个对象、69,078,298,624 B 处于 pending delete，看着够清出 17 GB/盘；授权后跑 `--delete`，扫描仍报 19,787 个对象 / 68,004,319,232 B，但 6 秒就退出，只删掉 137 个 pending slice，逐盘 `du` 合计只回收 **4,150,571,008 B**，盘 0 依然只剩约 4 GB 可用。

### 根因
`pending delete` 不等于「现在就能删」。JuiceFS 对刚写入/可能仍在引用的对象有安全窗口，且真正释放还牵涉元数据侧处理；日志里三条 `stop deleting slice` 就是被拦下的现场。删除轮的扫描数也比只读轮少（20,101→19,787 对象），两次扫描本身不是同一快照。

### 处置（命令）
```bash
# 1) 先只读取数，别直接 --delete
juicefs gc --threads 1 redis://10.20.2.169:6379/61
# 2) 授权后按同一 argv 加 --delete，逐盘记录前后 du，回收量以 du 差为准
for d in /srv/ax-objects-{0..3}; do echo -n "$d "; du -sx -B1 "$d"; done   # 删除前后各一次
```
不要把 gc 输出里的 `pending delete (…bytes)` 当作可回收量；盘 0 的门槛只能靠「等窗口过后再跑一次」「评估 `--compact`（改元数据，必须单独审核）」或扩容三者之一解决，都要新的 reviewed 决定。

### 证据
`evidence/preparation/b-c1-gc-maintenance-01/README.zh.md`（两次扫描原文 + 四盘 du 前后对比表，合计 4,150,571,008 B）；`…/gc-del-01/gc.stderr`（`scanned 19873 objects … 19787 pending delete (68004319232 bytes)`、`Deleted 137 pending slices`、三条 `stop deleting slice`）；只读轮原文见 `…/gc-18/gc.stderr`。

## 坑 11 `gc` 之外还有五条硬前提，缺一条整窗作废

### 症状
gc 跑完才发现「client 其实还在跑」、或 AOF 变了、或还有 JuiceFS 会话，于是这一窗的维护证据全部不能用——更糟的情况是删掉了正在写的对象。

### 根因
gc 直接改对象存储与元数据，任何在写客户端、任何残留会话都在与新状态竞争。所以安全前提必须**在 gc 之前逐条证明**，不能事后补。

### 处置（命令）
```bash
# ① client 全程停机：任何 StartInstance 之前先等 Stopped/StopCharging 落定
# ② AOF 前后逐字节一致（manifest + 每个 part 的 size/mtime/sha256）
ls -l /srv/ax-metadata/redis/appendonlydir; sha256sum /srv/ax-metadata/redis/appendonlydir/*
# ③ CLIENT LIST 里没有 JuiceFS/Go 会话（只应剩自己的探针）
redis-cli -h 10.20.2.169 CLIENT LIST | grep -Ei 'juicefs|go-redis' || echo 'no juicefs session'
# ④ 节点上没有 juicefs 进程、没有到元数据 Redis 的 established 连接
pgrep -a juicefs || echo 'no juicefs process'
ss -H -tn state established | grep '10.20.2.169:6379' || echo 'no redis session'
# ⑤ 收尾：objects/metadata 回 StopCharging，client 保持 Stopped
```
18 次实跑的收尾数据：AOF `changed: false`（manifest/两 part 的 size、mtime、sha256 全同）、`peers: 0`、`suspect: []`、gc 后无 juicefs 进程、无 established 连接、exit 0 未超时、client 全程 `Stopped`、两台回 `StopCharging`。

### 证据
`evidence/preparation/b-c1-gc-maintenance-01/README.zh.md`（`gc-18` 安全性小节与 `gc --delete` 小节的收尾）；`…/gc-18/node-metadata.jsonl`（`verify` 行的 `delta.changed:false`、`redis_clients.peers:0`）；[`b-c1-gc-node.py:377`](scripts/b-c1-gc-node.py#L377)（`verify_aof`）、[`:476`](scripts/b-c1-gc-node.py#L476)（`redis_clients`）、[`:521`](scripts/b-c1-gc-node.py#L521)（`run_gc` 的前置 require）。

## 下一轮开机检查清单

按顺序做，**第 6 步之前不要开计时**。

1. **核对实例与盘**（只读）：`python3 /srv/ax-pilot-control/code-19/experiments/unified-suite/pilot-aliyun/scripts/cloud.py inspect --out /srv/ax-pilot-control/inspect-$(date -u +%Y%m%dT%H%M%SZ)` —— 输出目录必须是新的，回执里逐条 `DescribeInstances/DescribeDisks`。核对 6 台实例、12 块数据盘：A = objects `i-bp1h9kaoya0predubdkn`（4×60 GiB `…6kvd/6kve/6kvf/6kvg` + 采样盘 `bp16jaiu2fcmbij8mg7i`）、metadata `i-bp1gy3cfuj9jik0y4oc3`（`…upt3k`）、client `i-bp1809mw7rlujtj9f705`（`…c8epi`）；B = objects 四盘 `bp1bhdngko7nq3c2i340…i343`、metadata `bp14nenf7osz9v1rkk5l`。
2. **挂盘**：A 用 [`a-pool-up.py`](scripts/a-pool-up.py) 按角色各跑一次（见坑 1）；B 由 [`b-c1-gc-node.py`](scripts/b-c1-gc-node.py) 的会话在 `snapshot` 之前按 serial/UUID 自己重挂 + 重建 bind，不再单独写脚本。挂完 `findmnt` 逐个确认，`df` 里必须看到 `/srv/ax-objects-{0..3}`、`/srv/ax-metadata`、`/srv/ax-client`。
3. **起服务**：`docker start ax-pilot-20261111-objects-01-rustfs-1`（等 `State.Running=true`）；`systemctl start ax-pilot-redis.service`（先用坑 2 的顺序清掉外来 redis）。
4. **核生效配置**：`CONFIG GET appendonly/databases/dir/bind` 四项全对，`ss -H -ltnp | grep ':6379'` 只剩原实例（坑 2、3、4）；RustFS 容器 `Image`、`NetworkMode=host`、四个 bind 与冻结记录逐项一致。
5. **取租约**：上传 guard 脚本 → `python3 -B` → 首行必须是 `held`；先 `ls -l /srv/ax-pilot-control/parallel-control-01/runner-locks` 确认没有上一轮残留（坑 6、7）。
6. **才开计时**：跑 [`c4-on1-preflight.py`](scripts/c4-on1-preflight.py) 生成 900 秒内的五件证据（坑 9），同一窗口内 `systemd-run` 启动 unit，并把 `invocation` 写进回执；归档阶段按坑 8 拆包，`gc` 类操作按坑 10、11 执行。
