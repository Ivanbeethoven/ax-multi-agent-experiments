# C4 本地 compose 首轮诊断

**执行通过；性能尚未验收。** randrw 两种模式的吞吐与 completion p99 改善；seqwrite 吞吐回退约 25%，randwrite direct=1 回退 64.3%。首轮按 baseline→C4 顺序执行；[调换顺序的纯写复测](../write-repeat-02/README.md)另行保留，不覆盖本轮。

| fio 场景 / 方向 | baseline → C4 吞吐（MiB/s） | 变化 | baseline → C4 clat p99（ms） | 变化 |
|---|---:|---:|---:|---:|
| fio-randrw-direct0 / read | 25.737 → 54.229 | +110.7% | 566.231040 → 123.207680 | -78.2% |
| fio-randrw-direct0 / write | 11.771 → 24.722 | +110.0% | 93.847552 → 6.193152 | -93.4% |
| fio-randrw-direct1 / read | 146.349 → 194.590 | +33.0% | 0.011328 → 0.008640 | -23.7% |
| fio-randrw-direct1 / write | 65.777 → 85.396 | +29.8% | 0.014016 → 0.008096 | -42.2% |
| fio-randwrite-direct0 / write | 203.157 → 207.969 | +2.4% | 81.264640 → 67.633152 | -16.8% |
| fio-randwrite-direct1 / write | 181.818 → 64.928 | -64.3% | 0.026752 → 0.023168 | -13.4% |
| fio-seqwrite-direct0 / write | 300.145 → 223.586 | -25.5% | 63.700992 → 193.986560 | +204.5% |
| fio-seqwrite-direct1 / write | 320.480 → 240.372 | -25.0% | 0.019072 → 0.027264 | +43.0% |

p99 是 fio `clat`（完成延迟），不是总延迟；原始 JSON 同时保留 `slat` 与 `lat`。例如 randwrite direct=1 的平均提交延迟从 21.978 ms 增至 61.589 ms，不能因 completion p99 较小就宣称总延迟改善。

两版本均完成 7 项检查并 exit 0；写后 drain、pending、dirty、buffer dirty 全为 0；各 3 次挂载自然 exit 0、卸载 exit 0。自有后端和 compose 项目容器已核验为 0。命令成功及排空完成不等于没有性能回退。

这是本地 WSL 单次诊断，未清宿主页缓存，保留既定 workspace-v1、512 MiB memory、buffered/direct 配置。不得与云端 AI trace 耗时混成同一指标。

[紧凑摘要及完整精度](summary.json) · [原始全部比较](comparison.tsv) · [原始文件 SHA256](source-file-hashes.json) · [状态](status.json) · [容器释放核验](cleanup-verification.json)
