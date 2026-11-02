# C1：普通 fio

固定工作量psync/depth=1，顺序1MiB、随机4KiB，read/write/randread/randwrite/randrw(75/25)。每个job一个文件；不time_based、不碰裸设备、不自动扩大数据集。主表同步write每次fsync，保留fio原始JSON+与wrapper wall。应用direct=0，实际FUSE direct-I/O/内核缓存路径需独立验收。

[fio原生文档](https://fio.readthedocs.io/en/latest/fio_doc.html)说明了fsync、randseed、verify等参数。这里固定seed与CRC校验，测量结束后的drain和冷读回仍单独执行，不能仅凭fio退出0证明远端持久化。

1. 从plan中的C1 params创建 [fio request](examples/fio.request.json)，填actualroot、fioSHA、Python和helper SHA、监督mount绑定。root是新专用workspace中的预建目录。
2. 用prepare初始化fixture；这一步位于asset导入。记录每file/job CRC header布局，完整verify一次，再冻结base。每cell从该base得到新工作区。
3. 启动全层观测，标记measure-start，通过cohort释放fio测量worker。
4. 测量后标记measure-end；强排空、普通卸载/真实退出、冷读回CRC、标记verify-end，收观测。

```bash
# 准备/校验只针对此request指定的数据，不会启动mount。
python3 -m bench.fio_workload --request "$REQUEST" --phase prepare --output "$OUT/fio-prepare"
python3 -m bench.fio_workload --request "$REQUEST" --phase verify --output "$OUT/fio-base-verify"

# 生成测量worker草稿，在executor上做，输出必须在mount外。
python3 -m bench.runtime.workloads --request "$REQUEST" --output "$OUT/fio-worker-draft"
# 按执行手册review step/worker/cohort，再由cohort运行一次。
```

`measure` phase也可用单步executor调用，但正式多Agent使用既有cohort屏障。`verify`读取全部文件，发生错误保留fio.json/stderr/exit.json，不重新初始化文件把错误抹掉。

原始分布见 `workload/fio.json` 的每job数据。读与写分开提取IOPS/带宽/latency，fsync统计独立列；报告总应用字节必须等于numjobs×io_bytes。fio的读写比例是原生混合调度行为，记录实际read/write counts，不声称精确每4次必3读1写（C3才使用精确3:1）。

fio准备和验证也有wall，按asset/verification收费。数据文件crc格式依赖block size，因此不复用不兼容fixture。布局G消融保持最终对象、读范围、seed与并发一致，每个新布局重新导入/持久化，检实际B/q后才能进入测量。
