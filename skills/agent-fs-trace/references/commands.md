# 完整执行命令

下面以Ubuntu 24.04/WSL2、x86_64为例。命令中的SKILL指本包解压后的agent-fs-trace目录。所有输出使用新目录。准备依赖、构建和预检不调用模型；第5节才会消耗模型token。

## 1. 系统依赖与路径

Docker daemon需事先可用，WSL可使用已启用WSL集成的Docker Desktop。普通容器内无挂载许可时直接转第6节。

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential pkg-config \
  libfuse2t64 fuse3 git curl pigz nodejs npm
docker info >/dev/null
test -c /dev/fuse
test -r /dev/fuse && test -w /dev/fuse
grep -Eq '^[[:space:]]*user_allow_other([[:space:]]|$)' /etc/fuse.conf
```

最后一项未通过时，由有管理权限的一方在/etc/fuse.conf启用user_allow_other，然后重新检查。设备检查不能代替实际挂载预检。

```bash
# Linux安装位置；本机WSL使用下一行替代：
export SKILL="${SKILL:-$HOME/.codex/skills/agent-fs-trace}"
# export SKILL=/mnt/c/Users/HXY/.codex/skills/agent-fs-trace
export RUNTIME="$SKILL/assets/runtime"
export LAB="$HOME/agent-fs-lab"
mkdir -p "$LAB/runs" "$LAB/fuse-helpers"
python3 -m venv "$LAB/.venv"
"$LAB/.venv/bin/pip" install fusepy==3.0.1 docker==7.2.0 pyarrow==26.0.0
export AX_TRACE_FUSE_HELPERS="$LAB/fuse-helpers"
# 给libfuse2提供fusermount名称，实际仍使用已验证的fusermount3。
test -e "$LAB/fuse-helpers/fusermount" || \
  ln -s "$(command -v fusermount3)" "$LAB/fuse-helpers/fusermount"
```

## 2. OpenCode、Rust与固定数据集

```bash
npm install --prefix "$LAB/opencode" opencode-ai@1.19.4
export AX_TRACE_OPENCODE="$LAB/opencode/node_modules/opencode-linux-x64/bin/opencode"
"$AX_TRACE_OPENCODE" --version

rustup toolchain install 1.98.1 --profile minimal
cargo +1.98.1 build --release --locked \
  --manifest-path "$RUNTIME/tools/full-fuse-replay/Cargo.toml" \
  --target-dir "$LAB/build"
export AX_TRACE_BINARY="$LAB/build/release/full-fuse-replay"
sha256sum "$AX_TRACE_BINARY"

export AX_TRACE_DATASET="$LAB/verified.parquet"
curl --fail --location --retry 2 \
  https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/resolve/78f471bf655a3137b2e8a75af1501690ec009ec3/data/test-00000-of-00001.parquet \
  --output "$AX_TRACE_DATASET"
printf '%s  %s\n' \
  030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25 \
  "$AX_TRACE_DATASET" | sha256sum -c -
```

本入口固定四道题。先检查并下载需要的镜像；只跑pytest时仅拉pytest镜像即可：

```bash
docker pull swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-10356:latest
docker image inspect swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-10356:latest \
  --format '{{.Id}} {{json .RepoDigests}}'
# 扩充时：
docker pull swebench/sweb.eval.x86_64.django_1776_django-10554:latest
docker pull swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25102:latest
docker pull swebench/sweb.eval.x86_64.sympy_1776_sympy-13878:latest
```

入口记录实际image_id。若标签不可下载，使用既有镜像的docker save/load或SWE-bench对应构建流程，核对输入与image_id后再跑，不随意替换环境。

filesystem-only不需要评分库。需要基线/参考补丁/Agent补丁评分时，另外安装：

```bash
git clone https://github.com/SWE-bench/SWE-bench.git "$LAB/SWE-bench"
git -C "$LAB/SWE-bench" checkout 02e7a74ffd0b707aab73d203fe87bdc7c76afc8e
"$LAB/.venv/bin/pip" install -e "$LAB/SWE-bench"
```

## 3. 固定运行环境

默认使用空状态缓存。复用依赖缓存会改变负载，必须记录来源摘要；缓存种子不得含真实认证信息。

```bash
export AX_TRACE_CACHE_SEED=""
declare -p SKILL RUNTIME LAB AX_TRACE_FUSE_HELPERS AX_TRACE_OPENCODE \
  AX_TRACE_BINARY AX_TRACE_DATASET AX_TRACE_CACHE_SEED > "$LAB/trace.env"
cat > "$LAB/capture-one.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/trace.env"
exec "$LAB/.venv/bin/python" "$RUNTIME/scripts/run_opencode_full_trace.py" "$@"
SH
chmod 700 "$LAB/capture-one.sh"
```

trace.env只存这些路径，不保存模型provider或密钥。OpenCode任务容器为4 CPU/6 GiB；模型和small_model均固定experiment/deepseek-v4-flash；无步数上限，3600秒超时，build Agent。webfetch/websearch/codesearch/task禁用；本机转发器使用host网络，以便容器访问宿主loopback。

状态映射：

| 宿主记录挂载中的路径 | 任务容器路径/环境变量 |
|---|---|
| mount/repo | /testbed |
| mount/state/home | HOME=/agent-state/home |
| mount/state/config | XDG_CONFIG_HOME=/agent-state/config |
| mount/state/data | XDG_DATA_HOME=/agent-state/data |
| mount/state/state | XDG_STATE_HOME=/agent-state/state |
| mount/state/cache | XDG_CACHE_HOME=/agent-state/cache |
| mount/state/config/opencode | OPENCODE_CONFIG_DIR=/agent-state/config/opencode |

## 4. 无模型预检

```bash
source "$LAB/trace.env"
"$LAB/.venv/bin/python" "$RUNTIME/scripts/full_trace_common.py" \
  "$LAB/runs/preflight-01"
cat "$LAB/runs/preflight-01/gate.json"
```

它执行子进程、SQLite WAL、mmap、目录遍历、rename、软/硬链接、chmod，再正常卸载并Rust回放。输出目录不能存在。任务入口还会为真实任务再做一次独立预检。

## 5. 新采集：先跑一题

本入口从stdin接收provider对象，结构为npm、models、options，其中options含baseURL/apiKey，models包含deepseek-v4-flash。使用已有OpenCode配置的provider.deepseek。不要把API key放命令参数，也不要用tee记录这条管道。

该入口使用provider中的实际URL和字面key；若配置用的是环境变量占位符，先在控制端内存中解引用，再经stdin传入。不要把真实key写入被记录的config目录。

Linux：

```bash
export OPENCODE_USER_CONFIG="$HOME/.config/opencode/opencode.json"
python3 -c 'import json,os,sys; c=json.load(open(os.environ["OPENCODE_USER_CONFIG"],encoding="utf-8-sig")); json.dump(c["provider"]["deepseek"],sys.stdout)' | \
  "$LAB/capture-one.sh" --filesystem-only \
  --instance-id pytest-dev__pytest-10356 \
  --output "$LAB/runs/pytest-01"
```

本机Windows PowerShell → WSL（先在WSL按第1—3节设置/home/hxy/agent-fs-lab）：

```powershell
$taskConfig = Get-Content "$env:USERPROFILE/.config/opencode/opencode.json" -Raw | ConvertFrom-Json
$taskConfig.provider.deepseek | ConvertTo-Json -Depth 12 -Compress |
  wsl -d Ubuntu-24.04 -- /home/hxy/agent-fs-lab/capture-one.sh `
    --filesystem-only --instance-id pytest-dev__pytest-10356 `
    --output /home/hxy/agent-fs-lab/runs/pytest-01
```

filesystem-only仅跳过额外评分，仍是真实Agent读代码、修改、运行工具。删去该参数会额外验证原始代码、参考补丁并评分模型补丁，需要第2节的评分库。

扩充时重复同一入口，替换instance-id和新输出目录。四个ID见repositories.md。首次逐题验收，资源允许后最多并发两题；每题独立FUSE挂载、转发端口、状态和初始目录。不要将任务失败自动当成重跑理由。

任务正常完成、超时或失败均保留退出状态。若控制器异常，保留backing/capture/Agent日志，先检查该任务容器是否还在运行、记录器是否仍挂载；只停止本次输出目录关联的资源，正常卸载后再尝试回放。恢复记录单独说明，缺失日志不能填造。

## 6. 不花token的已有数据回放

### GitHub分卷

```bash
git clone https://github.com/Ivanbeethoven/ax-multi-agent-experiments.git "$LAB/experiments"
git -C "$LAB/experiments" checkout dc48db5b4978aeffac7b33eb0386b27e95fe8f4e
"$LAB/.venv/bin/python" "$RUNTIME/scripts/replay_full_trace_bundle.py" \
  --bundle "$LAB/experiments/results/opencode-full-fuse-pytest-10356-20261023" \
  --output "$LAB/runs/portable-replay-01" --binary "$AX_TRACE_BINARY"
```

该步骤检查每卷/整体摘要、安全解压、初始树、Rust回放、最终树及字节/操作数。需要私有仓库权限；不需要Docker或模型。

### OSS四份完整归档

使用已配置身份的aliyun CLI；这不是实验RustFS账号。

```bash
export K="$RUNTIME/scenarios/k8s-containers"
export BASE=oss://brewfs-agent-traces-20261023-4ccdae73/full-fuse/20261023
mkdir -p "$LAB/archives" "$LAB/cases" "$LAB/workspaces"
aliyun oss cp "$BASE/pytest-full-02.tar.gz" "$LAB/archives/pytest-full-02.tar.gz" \
  --endpoint https://oss-cn-hangzhou.aliyuncs.com
python3 "$K/scripts/check_archives.py" "$LAB/archives" --cases pytest-full-02
test ! -e "$LAB/cases/pytest-full-02" || exit 1
tar --no-same-owner --same-permissions -xzf "$LAB/archives/pytest-full-02.tar.gz" -C "$LAB/cases"
python3 "$K/scripts/run_case.py" --case "$LAB/cases/pytest-full-02" \
  --workspace-parent "$LAB/workspaces" --out "$LAB/runs/oss-replay-01" \
  --binary "$AX_TRACE_BINARY" --run-id oss-replay-01 --worker-id local
```

先确认下载和SHA检查成功再解压。另三题下载文件名为django.tar.gz、sklearn.tar.gz、sympy.tar.gz，解压顶层目录同名；校验命令的cases参数与目录名一致。全部压缩约3.86 GiB；完整解压含日志、初始/最终/回放副本，建议至少80 GiB可用空间。逐题重放为串行文件负载，chown等需原操作所需权限。

### 回放自己刚采集的完整任务

run_opencode_full_trace.py结束时已经自动回放。需要另做新副本时：

```bash
export CASE="$LAB/runs/pytest-01"
mkdir -p "$LAB/workspaces"
python3 "$RUNTIME/scenarios/k8s-containers/scripts/run_case.py" \
  --case "$CASE" --workspace-parent "$LAB/workspaces" \
  --out "$LAB/runs/own-replay-01" --binary "$AX_TRACE_BINARY" \
  --run-id own-replay-01 --worker-id local
```

## 7. 统计、核验与归档

```bash
python3 "$RUNTIME/scripts/analyze_full_fuse_trace.py" \
  "$CASE/capture/events.jsonl" "$CASE/metrics-recomputed.json"
python3 "$SKILL/scripts/check_trace.py" --source "$CASE"
python3 "$SKILL/scripts/package_trace.py" --source "$CASE" \
  --output "$LAB/exports/pytest-01" --credential-config "$OPENCODE_USER_CONFIG"
sha256sum "$LAB/exports/pytest-01/"*.tar.gz
```

check_trace写skill-check.json，并核对操作/读写字节、硬链接拓扑和SQLite副本。package_trace保留整个已停止目录，用pigz压缩，生成逐文件清单与SHA256。credential-config是完整OpenCode配置，不是单独provider对象；Windows配置可使用/mnt/c/Users/HXY/.config/opencode/opencode.json路径。已知key扫描失败即停止，不替换真实字节。

上传应在用户指定目标后执行，例如使用私有OSS前缀。先核对本地manifest与SHA256，再上传，再通过远端大小/摘要或下载回读核验；采集器不自动发布数据。本包没有运行任何新模型任务或新OSS上传。

## 8. 换成其他Agent或自己的任务

使用同一记录器CLI：

```bash
# RUN为新目录，且已准备RUN/backing/repo和RUN/backing/state。
export RUN
mkdir -p "$RUN/mount"
cp -a "$RUN/backing" "$RUN/initial"
PYTHONPATH="$RUNTIME/scripts" python3 - <<'PY'
import os
from pathlib import Path
from full_trace_common import tree, save
root = Path(os.environ["RUN"])
save(root / "initial-tree.json", tree(root / "initial"))
PY
export PATH="$AX_TRACE_FUSE_HELPERS:$PATH"
"$LAB/.venv/bin/python" "$RUNTIME/scripts/full_fuse_record.py" \
  --root "$RUN/backing" --mount "$RUN/mount" --output "$RUN/capture"
```

记录器前台运行；任务在另一终端/控制器中启动，repo/state绑定和环境变量按第3节设置。停止所有任务进程后执行`fusermount3 -u "$RUN/mount"`并等待记录器正常退出。在没有活动写者的前提下生成最终清单：

```bash
PYTHONPATH="$RUNTIME/scripts" python3 - <<'PY'
import os
from pathlib import Path
from full_trace_common import tree, save
root = Path(os.environ["RUN"])
save(root / "final-tree.json", tree(root / "backing"))
PY
python3 "$RUNTIME/scripts/analyze_full_fuse_trace.py" \
  "$RUN/capture/events.jsonl" "$RUN/metrics.json"
test ! -e "$RUN/replay-new" || exit 1
cp -a "$RUN/initial" "$RUN/replay-new"
"$AX_TRACE_BINARY" "$RUN/capture/events.jsonl" "$RUN/capture/blobs" \
  "$RUN/replay-new" "$RUN/replay-new.json"
PYTHONPATH="$RUNTIME/scripts" python3 - <<'PY'
import json, os
from pathlib import Path
from full_trace_common import tree
root = Path(os.environ["RUN"])
assert tree(root / "replay-new") == json.loads((root / "final-tree.json").read_text())
print("final tree matches")
PY
```

直接Rust接口为：

```text
full-fuse-replay EVENTS.jsonl BLOBS_DIR NEW_INITIAL_COPY NEW_REPORT.json
```

新任务需自行提供初始/最终树、退出状态、工具与输入摘要，以及相应验收入口；四题专用脚本不能只改instance-id就支持任意仓库。Agent原生对话日志用于理解任务，events.jsonl＋blobs＋initial才是文件回放输入。
