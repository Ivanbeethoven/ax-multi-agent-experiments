# B 组 C2 JuiceFS：发布草稿

仅准备，尚未执行。正式 C2 已完成，三份角色包与原六轮校准已在协调节点封存。当前 B 组 C1 已运行；以下传输、扫描和读回均应等协调节点确认 **A、B 两组均不在计时或冷读验证阶段** 后再做。无需访问工作节点、补跑或重新打包。

协调节点已用原 `05357b5089cd679c-r1-juicefs.close.json`、各角色 `*-check.json`、六轮 `pool-b-juicefs-c2-01-archive-check.json` 和实际文件大小交叉核对。四包共 139,424,013 B。草稿时没有重新读取或散列归档。

```text
# name                    bytes      SHA256
client.tar.gz              9379100   c13751f131a9313060f5942acbde8d10882a1d7ac6897ef5799da821a440a0ce
metadata.tar.gz            2564152   ef448a53c90a416e9cb8e1d730c66467beb0c2191d664f0a85a9aa2d9fa4fbae
objects.tar.gz            20369828   90aa93cb6a63aabff1946d271cd116e46c55951d7eb3574378cab98bda0100a5
calibrations.tar.gz      107110933   664aec4c0ef443afa98db6265569e8d936af106e9cd9a2c85de72a590a71243a
```

## 上传：从本机 WSL 的实验仓库根目录运行

沿用已发 C2 native 与 C4 JuiceFS 的逐对象流程：既有工具扫描包内凭据、检查源 SHA、禁止覆盖，从协调节点经杭州 OSS 内网传输，逐包做内网全量读回和本机匿名公网 HEAD。若中途失败，先核对已写对象和已有回执；不能直接重跑整块。

```bash
set -euo pipefail
script=experiments/unified-suite/pilot-aliyun/scripts/archive-cloud-to-oss.py
printf '%s  %s\n' 0ed3ff910b019133b9c6fdfe4ec480eaf4563614c9c2e430866994673d1337fd "$script" | sha256sum -c -
cache=/mnt/c/Users/HXY/.cache/ax-pilot-20261111
receipts="$cache/b-c2-oss-receipts-01"
mkdir -- "$receipts"

control=/srv/ax-pilot-control/parallel-control-01
cell=05357b5089cd679c-r1-juicefs
prefix="pilot-results/20261111/c2-$cell"
while read -r name sha; do
  source="$control/cells/$cell/archives/$name"
  if [[ "$name" == calibrations.tar.gz ]]; then
    source="$control/retained-calibrations/pool-b-juicefs-c2-01.tar.gz"
  fi
  python3 -B "$script" --source "$source" --sha256 "$sha" --object "$prefix/$name" \
    --ssh /mnt/c/Windows/System32/OpenSSH/ssh.exe \
    --ssh-config C:/Users/HXY/.cache/ax-pilot-20261111/bootstrap-ssh.conf \
    --alias ax-pilot-jump --credentials /mnt/c/Users/HXY/.aliyun/config.json \
    --out "$receipts/$name.json"
done <<'PINS'
client.tar.gz c13751f131a9313060f5942acbde8d10882a1d7ac6897ef5799da821a440a0ce
metadata.tar.gz ef448a53c90a416e9cb8e1d730c66467beb0c2191d664f0a85a9aa2d9fa4fbae
objects.tar.gz 90aa93cb6a63aabff1946d271cd116e46c55951d7eb3574378cab98bda0100a5
calibrations.tar.gz 664aec4c0ef443afa98db6265569e8d936af106e9cd9a2c85de72a590a71243a
PINS
```

## 独立回读：上传成功后，在协调节点的新目录运行

独立发起一次无签名 GET，校验文件大小和 SHA，再用固定版本核验外层与所有嵌套包。输入是 **OSS 下载副本**，不是原归档路径；本机无需下载大包。结果分别在 `*-check/result.json`。不要在尚有计时或冷读任务时运行。

```bash
set -euo pipefail
verifier=/srv/ax-pilot-control/code-15/experiments/unified-suite/pilot-aliyun/scripts/verify-retained-bundle.py
printf '%s  %s\n' 1f97cb166803486f4f771061360c4c8b71fdf278f91fb757928bb4ccf20f94c8 "$verifier" | sha256sum -c -
out=/srv/ax-pilot-control/preparation/b-c2-oss-readback-01
mkdir -- "$out"
endpoint=https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou-internal.aliyuncs.com
prefix=pilot-results/20261111/c2-05357b5089cd679c-r1-juicefs
while read -r name bytes sha; do
  nice -n 15 ionice -c 3 curl --fail --silent --show-error --proto '=https' \
    --output "$out/$name" "$endpoint/$prefix/$name"
  test "$(stat -c %s "$out/$name")" -eq "$bytes"
  nice -n 15 ionice -c 3 python3 -B "$verifier" \
    --archive "$out/$name" --sha256 "$sha" --out "$out/${name%.tar.gz}-check"
done <<'PINS'
client.tar.gz 9379100 c13751f131a9313060f5942acbde8d10882a1d7ac6897ef5799da821a440a0ce
metadata.tar.gz 2564152 ef448a53c90a416e9cb8e1d730c66467beb0c2191d664f0a85a9aa2d9fa4fbae
objects.tar.gz 20369828 90aa93cb6a63aabff1946d271cd116e46c55951d7eb3574378cab98bda0100a5
calibrations.tar.gz 107110933 664aec4c0ef443afa98db6265569e8d936af106e9cd9a2c85de72a590a71243a
PINS
```

两个步骤都通过后，再用四份上传回执、独立核验摘要和原关闭回执更新数据清单。旧回执保持不变。
