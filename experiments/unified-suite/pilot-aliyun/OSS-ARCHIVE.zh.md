# 云端结果归档

C2 native 的正式三角色原始记录和六次校准包已公开，共 **56,322,182 字节**。下载地址、SHA256 和逐次上传回执见 [index.json](evidence/cloud-20261111/05357b5089cd679c-r1-native/oss/index.json)。每个包均完成 OSS 内网全量读回校验和公网匿名 HEAD 检查。原云端文件保留。

C3 JuiceFS 十万文件的正式三角色原始记录和原六次校准已公开，共 **35,354,910 字节**。下载地址及 SHA256 见 [C3 JuiceFS 下载清单](evidence/c3-jfs-01/oss/index.json)。校准复用了原始记录。

C3 BrewFS flat 十万文件的正式三角色原始记录、关闭回执和原六次校准已公开，共 **29,801,805 字节**，见[下载清单](evidence/c3-flat-01/oss/index.json)。

C1 BrewFS [随机读](evidence/c1-native-randread-02/oss/index.json)和[混合读写](evidence/c1-native-randrw-01/oss/index.json)的正式三角色原始记录、关闭回执和两组原六次校准已公开。

C4 JuiceFS 第一轮关采集校准的[原始记录和关闭回执](evidence/c4-juicefs-calibration-off1-01/oss/index.json)已公开。该轮在冷读通过后补收原档，没有重跑。两轮[开采集校准原档](evidence/c4-juicefs-calibration-on12-01/oss/index.json)也已公开。第四轮[关采集校准原档和补收回执](evidence/c4-juicefs-calibration-off2-01/oss/index.json)也已公开，没有重跑。校准均未计入正式结果。

| 文件 | 内容 |
|---|---|
| client.tar.gz | 正式客户端、挂载、回放、校验和采集原档 |
| metadata.tar.gz | 正式 Redis 和宿主采集原档 |
| objects.tar.gz | 正式 RustFS 和宿主采集原档 |
| calibrations.tar.gz | 原六次采集开销校准及关联脚本、关闭证明 |

上传使用 [archive-cloud-to-oss.py](scripts/archive-cloud-to-oss.py)，在本机执行，数据由协调者直接走杭州 OSS 内网传输。本机只接收 JSON 回执。脚本核验指定源 SHA、检查包中凭据、禁止覆盖已有对象，再将单个对象设为公开只读。

```bash
python3 experiments/unified-suite/pilot-aliyun/scripts/archive-cloud-to-oss.py \
  --source "$CLOUD_ARCHIVE" --sha256 "$SHA256" \
  --object "pilot-results/20261111/$CELL/$NAME" \
  --ssh-config "$SSH_CONFIG" --alias ax-pilot-jump \
  --credentials "$HOME/.aliyun/config.json" --out "$RECEIPT"
```

`CLOUD_ARCHIVE`必须位于协调者`/srv/ax-pilot-control`，`RECEIPT`必须是新文件。Windows 本机没有 Python 时可在 WSL 执行，并用`--ssh /mnt/c/Windows/System32/OpenSSH/ssh.exe`及 Windows SSH 配置路径。

AccessKey secret留在本机，只通过SSH标准输入向协调者发送15分钟有效的单次PUT签名链接；链接中含AccessKey标识，未写入磁盘或回执。早期回执中的`credentials_sent_to_cloud=false`表示未传送secret。新回执用更明确的字段分别记录secret和标识。服务器保存的上传结果只含公开下载地址、大小、SHA和请求ID。
