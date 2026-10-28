# 多后端测试流水线协议

本协议描述 `paper-design/pipeline/` 的一批可复现实验。每批先通过资格门，再运行 W1--W4；失败批次保留产物和状态，不能把空值填成零，也不能用另一分支或 `latest` 镜像补跑。

## 前置条件

运行主机必须满足以下条件；同一批的主机、后端、客户端和缓存策略要保持一致。

- 8 vCPU、至少 30 GiB 内存、至少 15 GiB swap，同一区域同规格。
- 四块独立的 **250 GiB ESSD PL1** 数据盘（4×250 GiB PL1）。系统盘可为 100 GiB PL1，但其约 6,800 IOPS/170 MB/s 只用于脚本、日志和结果，不能承载任何测量 I/O。
- 语料、fio 目标文件、BrewFS/JuiceFS cache 目录和 RustFS 数据都落在数据盘。RustFS 必须使用四个 volume，每块数据盘一个（例如 `/work/d1`--`/work/d4`），否则 EC 分片和动态块证据无效。
- Redis 正常运行。BrewFS 使用 `redis://127.0.0.1:6379/0`，JuiceFS 使用 database 1；元数据后端必须是 Redis，不能使用 `sqlite::memory:`。
- `bash`、Python 3、fio 3.36、Docker、`fusermount3`、对应的 BrewFS/JuiceFS 客户端均可执行。挂载点、数据盘目录、cache 和语料目录可写。
- RustFS 来源固定为 `https://github.com/Ivanbeethoven/rustfs` 的 `feat/dynamic-block-size-rebased`。每批开始必须执行 `git ls-remote` 并把完整 SHA 写入 `backend-pin.json`；镜像 label `org.opencontainers.image.revision` 必须机械等于该 SHA，镜像 digest 必须是不可变 `sha256:` 值。BrewFS 客户端 SHA 单独记录。

部署前检查盘落点，例如：

```bash
nproc                         # 8
free -g                       # total >= 30，swap >= 15
bash paper-design/tools/check-disk-layout.sh /work
```

`check-disk-layout.sh` 发现语料、目标文件或 cache 落到系统盘时返回非零。G2 裸盘标定必须先于性能结论，使用数据盘上的 ext4 路径。

## 配置名称

流水线接受归档产物使用的短名和脚本内部的长名：

| 短名 | 内部名 | 含义 | 元数据 Redis |
|---|---|---|---|
| `c2` | `flat-v1` | BrewFS flat/local-fs | db0 |
| `c3` | `overlay-native` | BrewFS overlay + native lower | db0 |
| `c4` | `overlay-packed` | BrewFS overlay + packed lower | db0 |
| `c5` | `juicefs` | JuiceFS 对照 | db1 |

短名映射是脚本中的显式映射；生成的 manifest 使用内部名。`c3` 是本文完整命令示例。

## 一组命令跑完 C3

先准备本批真实 pin、镜像和证据。下面的目录名只是示例，实际目录必须位于正确的数据盘；`$image` 必须是从 `backend-pin.json.frozen_commit` 构建的镜像。

安全 dry-run 可以只运行这一条：

```bash
paper-design/pipeline/run-all.sh --config c3 --dry-run
```

它不调用 Docker、Redis、fio、BrewFS、JuiceFS、FUSE，也不创建目录或产物；默认只打印 `./pipeline-dry-run/overlay-native` 作为预期输出目录。带完整路径的 dry-run 可用于审核真实命令：

```bash
paper-design/pipeline/run-all.sh --config c3 \
  --pin /work/results/c3/backend-pin.json \
  --output-dir /work/results/c3 \
  --mount-point /work/mnt-c3 \
  --data-dir /work/d1/c3-data \
  --data-disk /work/d2 \
  --image "$image" \
  --owner exp-c3 \
  --rustfs-evidence /work/evidence/c3-ec-facts.json \
  --brewfs /home/hxy/bin/brewfs-975deac \
  --dry-run
```

确认 dry-run 的命令和路径后，去掉最后的 `--dry-run` 执行真实批次：

```bash
paper-design/pipeline/run-all.sh --config c3 \
  --pin /work/results/c3/backend-pin.json \
  --output-dir /work/results/c3 \
  --mount-point /work/mnt-c3 \
  --data-dir /work/d1/c3-data \
  --data-disk /work/d2 \
  --image "$image" \
  --owner exp-c3 \
  --rustfs-evidence /work/evidence/c3-ec-facts.json \
  --brewfs /home/hxy/bin/brewfs-975deac
```

真实运行需要 Redis、RustFS 四卷、数据盘和已构造的测量证据；dry-run 不会绕过这些要求。

## 每一步及期望输出

1. **批次 pin。** `run-all.sh` 先复制 pin 到输出目录，再执行
   `git ls-remote https://github.com/Ivanbeethoven/rustfs refs/heads/feat/dynamic-block-size-rebased`。
   期望 `backend-pin-remote.txt` 中的远端 SHA 等于 `frozen_commit`，并保留
   `repository`、`branch`、`frozen_commit`、`image_reference`、`image_digest`、`built_from_commit`、`resolved_at`、`brewfs_client_sha`、`resolved_by`。
2. **G0 镜像门。** 期望 `docker inspect -f '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image"` 打印冻结 SHA，且 RepoDigest 包含 pin 中的 digest；`g0.txt` 写入 `G0 pass: <sha>`。
3. **G1 挂载和基本语义。** C3 先执行 `workspace init-volume`，解析输出中的 32 位十六进制 workspace UUID，再用该 UUID 挂载。挂载点执行 write、`sync`、readback、`stat`、`ls`、rename、unlink；`g1-output.txt` 保存每条字面命令、输出和 exit code，成功时末尾为 `G1 pass`。
4. **G2 裸盘上限。** 在 `--data-disk` 上以 `rw=write, bs=1M` 和 `rw=randread, bs=4k`、`direct=1,numjobs=8,iodepth=32,runtime=20s` 运行 fio。期望 `g2-seq-write.json`、`g2-rand-read.json` 和 `ceilings.json`，并看到 `G2 pass; ceilings=...`。W1 超过相应上限的格标为 `void(cache-hit)`，不能当作后端能力。
5. **G3 RustFS 证据。** `--rustfs-evidence` 必须包含实测 `object_occupancy`、布尔 `internal_ec_read_quantum_present` 和响应中的 `effective_ec_block_size_bytes`。期望 `ec-facts.json` 保存输入、记录时间和实测值，`g3-output.txt` 写入 `G3 pass`。环境变量为 true 本身不算证据。
6. **W1--W4。** 依次生成 `w1.csv`、`w2.csv`、`w3.csv`、`w4.csv`。W1 使用 1 MiB 顺序和 4 KiB 随机、8 jobs、depth 32、direct I/O；W2 记录 cold/hot 及 `read_bytes`；W3 以第二次应用层读取字节数为判据；W4 记录 FUSE/stat/cache 事实，stat IOPS 超过 100,000 标为 `void(kernel-cache)`。
7. **校验和汇总。** 所有 CSV 必须通过严格的 11 列 `validate.py`。最后写 `run-manifest.json`、`summary.md` 和 `summary.json`；汇总表按 workload、参数和配置给出 min/median/max，并带 `cache-hit`/`kernel-cache` 标签。

成功批次的主要产物为：`backend-pin.json`、`backend-pin-remote.txt`、`g0.txt`、`g1-output.txt`、`g2-output.txt`、`g3-output.txt`、`g2-*.json`、`ceilings.json`、`ec-facts.json`、`w1.csv`--`w4.csv`、`run-manifest.json`、`summary.md`、`summary.json`。

## Gate 失败处理、退出码和产物

以下是 `run-all.sh` 的真实行为；直接调用 `gates.sh gN` 时，单独的 gate 失败返回非零（通常为 1）。`run-all.sh` 的 G1 资格失败特意返回 0，以便把这批安全归档为 `unmeasured`；G0/G2/G3 返回 1 阻断后续测量。

| Gate | `run-all.sh` 失败退出码 | 失败时写入的产物和后续动作 |
|---|---:|---|
| G0 | 1 | 已创建输出目录并复制 `backend-pin.json` 时，`g0.txt` 捕获 inspect/校验输出，`gate-failure.txt` 写 `unmeasured(G0: see g0.txt)`；不挂载、不运行 W1--W4。pin 不合法或分支移动时，`backend-pin.json` 写 `blocked_reason`，`backend-pin-remote.txt` 保留远端检查输出。 |
| G1 | 0 | `g1-output.txt` 保存挂载/基本语义失败原因，`run-manifest.json` 的 `status` 为 `unmeasured(G1: ...)`，并跳过 G2、G3 和 W1--W4。packed 的 catalog capability 失败也走此路径，绝不静默退回 flat。 |
| G2 | 1 | `g2-output.txt` 保存 fio 命令及失败输出；若已完成的 JSON 存在则保留，`run-manifest.json` 写 `unmeasured(G2: see g2-output.txt)`；不启动 W1--W4。 |
| G3 | 1 | `g3-output.txt` 保存缺失字段或证据校验错误，`run-manifest.json` 写 `unmeasured(G3: see g3-output.txt)`；已有 `ceilings.json` 保留，不启动 W1--W4。 |

因此，退出码 0 不等于有可用性能数据：必须同时检查 manifest 的 `status` 和各 CSV 的 `status`。任何占位符 `REPLACE_WITH` 或 `REGISTRY/` 在 pin、数据配置或部署 spec 中都直接失败。

## CSV 回归自测

仓库中的 `fixtures/` 是 2026-10-28 的 15 个真实 CSV；`fixtures/ceilings.json` 是同批 C2 G2 裸盘产物，`fixtures/bad/` 有少列、多列和非法 status 三个故意失败样本。入口会逐项检查返回码，并用同一 ceilings 生成跨配置表：

```bash
paper-design/pipeline/selftest.sh
```

也可以单独执行：

```bash
python3 paper-design/pipeline/validate.py paper-design/pipeline/fixtures/*.csv
python3 paper-design/pipeline/summarize.py paper-design/pipeline/fixtures/*.csv \
  --ceilings paper-design/pipeline/fixtures/ceilings.json
```

`SELFTEST-OUTPUT.txt` 必须来自实际运行，不得手写或用历史日志代替。

## 已知失败清单

完整背景和对应脚本检查见 [`pipeline/known-issues.md`](pipeline/known-issues.md)。执行者必须特别处理以下问题：

- `--workspace` 参数必须是初始化后解析出的 32 位 UUID，不能传 `0` 或任意占位值。
- `sqlite::memory:` 只在单进程内存在，跨进程或 remount 不共享 catalog；元数据后端必须 Redis。
- packed workspace 可能返回 `packed workspace binding requires complete catalog permission/ID/lifecycle capabilities`，这批应记录 `unmeasured(G1: ...)`。
- packed initializer 忽略 `data-config.dir`（`data-config` 的 `dir` 字段），它会从当前工作目录下的 `./data/<manifest-key>` 查找；记录 `--init-cwd`、命令和完整输出。
- 默认 `B=1MiB` 时 `frame_size == shard_size`，动态块选择成为 no-op；必须记录运行时实际 B/q 和布局，不能只看开关。
- 系统盘约 6,800 IOPS，不得承载语料、fio、cache 或 RustFS volume 的测量 I/O。
- CSV 的 `params`/`note` 可能含逗号，必须由 Python `csv` writer/reader 正确引用；裸逗号会被解析成额外列并由 `validate.py` 拒绝。

历史批次的真实版本、SHA 和失败结果原样保留；`paper-design/design.json` 的 `observed_commit` 只是观测值，不是任何批次的 pin。
