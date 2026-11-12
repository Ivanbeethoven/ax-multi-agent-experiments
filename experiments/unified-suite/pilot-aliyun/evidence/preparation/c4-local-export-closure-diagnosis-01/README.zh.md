# C4：OpenCode 导出后的进程退出

同一份 894 项 checkpoint、同一个 OpenCode 1.2.26 二进制，在 WSL ext4 上只运行了一次 `opencode export`。使用私有检查点副本、空 HOME、工作区内的 XDG 目录和仅有 `lo` 的网络；没有调用模型。导出 10,734 字节，与云端 A、B 保存的原始导出 SHA-256 相同。

主进程退出时，原进程组仍有 `git gc --prune=7.days` 和它的 `git reflog expire --all` 子进程。Git 随后 repack/prune；工作区内快照 Git 库的 7 个 pack 相关文件，修改时间都在主进程退出后、Git 退出前。最后一个 Git 进程在主退出后 160.219 ms 自然退出；两次连续空组采样在 186.006 ms 完成。没有向该组发信号。[OpenCode 1.2.26 的快照维护代码](https://github.com/anomalyco/opencode/blob/v1.2.26/packages/opencode/src/snapshot/index.ts#L20-L49) 与捕获的命令一致。绝对时间和 CPU tick 见 [诊断回执](receipt.json)；原始进程记录及导出留在本机私有目录。

云端 [第一次 A 源端](../c4-state-reopen-a-b-source-01/source-export-exit.json) 主进程退出码为 0，但当时仍有两个进程；[第二次 A 源端](../c4-state-reopen-a-b-result-01/source-native-exit.json) 自然收尾用了 3.450 s。[同次 B 目标端](../c4-state-reopen-a-b-result-01/target-native-exit.json) 读回了同一份会话，5 s 时组内仍有进程，资格测试失败；[稍后探针](../c4-state-reopen-a-b-result-01/target-closure-probe.json) 才记录空组、空槽及无挂载。云记录只有 PID，没有子进程命令或 CPU，不能把本机的 Git 原因或时间套到云端 B。B 的超时在本机没有复现。

以后两套系统用同一计时定义：记录 OpenCode 启动、主进程退出、进程组自然退出、导出校验完成；再核对文件持久化、零上传债务、正常卸载和空执行槽。应另外报告“主退出 → 组退出”这段时间。单机 runner 用原有 90 s 覆盖主进程和后台进程，超时另记清理，发信号后空组仍算失败。历史单机回执可能经过清理，不能证明自然退出；旧云端 5 s 判定和证据保持原样。本次诊断不计入 C4 正式完成数。

本机重现入口（要求原检查点、二进制与回执 SHA 匹配，以及 root、`strace`、Linux namespace；输出目录必须不存在）：

```bash
sudo python3 -B experiments/unified-suite/pilot-aliyun/scripts/diagnose-c4-export-closure.py \
  --archive /absolute/path/checkpoint.tar.gz --receipt /absolute/path/receipt.json \
  --binary /absolute/path/opencode --out /absolute/path/new-diagnosis
```
