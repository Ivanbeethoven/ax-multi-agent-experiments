# C4：JuiceFS 原生 clone 本机资格

通过。使用 JuiceFS 1.4.2、独立的本机 Redis 和 RustFS，复用 [BrewFS fork 资格](../c4-workspace-v1-packed-fork-01/receipt.json)的两个文件，共 77 字节。两次执行 `juicefs clone -p --threads 1 <mount>/parent <mount>/child-N`，两个子目录的路径、文件长度和 SHA-256 与父目录一致，各有独立 inode。

只在 `child-1` 覆盖 `a.txt`、把 `sub/b.txt` 改名。父目录和 `child-2` 未变化。正常卸载后，启动一个全新的 FUSE 客户端并关闭客户端缓存和预读，三个目录的内容、路径和 inode 均与卸载前一致。Redis 与 RustFS 没有重启。两次挂载均正常退出，容器和临时目录已清理。

[实测回执](receipt.json) · [复现脚本](smoke.py)。在 WSL 用同一份已缓存的 JuiceFS 二进制和两个固定镜像运行：

```bash
sudo -n python3 experiments/unified-suite/pilot-aliyun/evidence/preparation/c4-local-juicefs-clone-01/smoke.py \
  --out /tmp/c4-juicefs-clone-new.json
```

如果二进制位置不同，传 `--juicefs /绝对路径/juicefs`；脚本仍会检查 SHA-256。单机功能资格，未计时，不是正式样本；未调用模型或使用云资源。
