# 两池执行前检查

控制目录：`/srv/ax-pilot-control/parallel-control-01`。计划含19项C1/C2/C3，C3为100,000×4KiB。BrewFS在pool-a，JuiceFS在pool-b；共用一个状态库，旧批次保留。

## 当前硬件记录

- 通过：`hardware-qualification-02/result.json`，SHA256 `fcdfe806fb7b5d77710ae89b0359590c6ec540454750cb19c1697b072f1ebbce`。
- 76项检查、20个来源文件。绑定`runtime-12-01/fleet.json`，SHA256 `f85605a02eea141c110c79cfb794bd2acaa83fc80b081cad7619f355ebc3aef1`。
- 原始6实例、12数据盘API结果和节点spec在`hardware-inputs-01/data/`；SSH原始值在`hardware-qualification-02/`。
- `hardware-qualification-01/`保留首次失败：A旧Redis记录只有IOAccounting结果，与新部署记录格式不同。第二次用原记录及当前PID、执行文件SHA、版本、CONFIG、cgroup检查，没有补写旧记录或改服务。

API输入格式为`cloud.py inspect`输出的`{instances:[...],disks:[...]}`。精确查询时设置`PageSize=100`，并核对返回ID集合等于inventory，避免DescribeDisks默认只返回10块盘。

`qualify-parallel-hardware.py`需要`--control --inventory --api-inventory --services --out`；有独立runtime时同时传`--runtime-control binding.json --runtime-control-sha256 SHA`。每次使用新输出目录。

`--services`文件格式：

```json
{
  "pool-a": {
    "objects": {"compose": "/实际/compose.json", "result": "/实际/result.json", "image_proof": "/实际/rustfs-image.json"},
    "metadata": {"result": "/实际/Redis结果.json"},
    "node_specs": {"client": {"path": "/协调者/client-spec.json", "sha256": "实际SHA256"}, "metadata": {"path": "/协调者/metadata-spec.json", "sha256": "实际SHA256"}, "objects": {"path": "/协调者/objects-spec.json", "sha256": "实际SHA256"}}
  },
  "pool-b": {"objects": {}, "metadata": {}, "node_specs": {}}
}
```

两池均填实际路径。服务文件来自对应远端主机，node spec来自协调者。工具不读取私密env文件；Docker只输出指定字段和四个布局开关。型号、磁盘、版本、资源限制缺失或不符则失败。相同云规格及guest CPU字符串不能证明同速或物理服务器独占；每项仍须单独完成六轮采集开销校准。

## B的C3容量记录

`capacity-b-01/before-raw/`保存04:50:03Z三角色原始记录及Redis INFO；`preparation-started.json`为04:54:51Z。`before.json`只含当时真实可用字节、可用inode和设备身份，没有补造总量或已用量。

```bash
python3 capacity-check.py import-before \
  --control "$CONTROL" --pool pool-b \
  --runtime-control "$RUNTIME/binding.json" --runtime-control-sha256 "$RUNTIME_SHA" \
  --source-directory "$OUT/before-raw" \
  --preparation-started "$OUT/preparation-started.json" --out "$OUT/before.json"
```

C3完成全部冷读校验、两次正常卸载且槽位空后，暂停该池其他写入，再采after：

```bash
python3 capacity-check.py snapshot \
  --control "$CONTROL" --pool pool-b \
  --runtime-control "$RUNTIME/binding.json" --runtime-control-sha256 "$RUNTIME_SHA" \
  --before "$OUT/before.json" --objects-container "$RUSTFS_CONTAINER_ID" \
  --preparation-result /srv/ax-client/preparation/parallel-c3-juicefs-01/run/result.json \
  --out "$OUT/after.json"
python3 capacity-check.py budget --before "$OUT/before.json" --after "$OUT/after.json" \
  --extra-full-copies 7 --reserve 0.2 --out "$OUT/budget.json"
```

用`before可用量−after可用量`计本次增长；以after原生总量检查再加7份完整副本后仍留20%。Redis内存和AOF分别列出增量，AOF已计入元数据盘预算。设备serial、容量、boot、服务PID或库存变动即拒绝。空间增加、AOF缩小也拒绝，防止清理或重写掩盖导入增长。旧total/used格式仍支持。

实际结果：`capacity-b-01/budget.json`通过，SHA256 `a629c44a5c8bc159e01003f2a0e81393fa282bc94faf52aebca81945c08e1493`。在C3原100,000文件完整导入、冷读检查、两次正常卸载结束后采集，其他写入暂停至采集结束。四块对象盘合计增加3,295,756,288字节；元数据盘增加30,310,400字节；Redis内存增加40,145,488字节，AOF增加28,370,315字节。额外7份完整副本后，四块对象盘各仍约有53.3GB可用，Redis预计使用322,261,880字节；14项检查均满足20%余量。`after.json`的SHA256为`bd88306baa1d2773ece2d13aed0b7ab2f17dde52a2c1e73823b5e1ad6528136f`。

## JuiceFS客户端记录

JuiceFS是固定的官方1.4.2客户端，不加入自编译清单。`attest-client-binary.py`读取安装文件的SHA和版本、原始挂载命令及其mount spec引用，核对原始route与资格目录的transfers。输出新增`juicefs/client-binary.json`和`juicefs/client-binary/`原始记录，再直接调用指定的`admit-c1.py`检查；不挂载、不claim、不运行负载。

```bash
python3 attest-client-binary.py \
  --qualification "$Q/juicefs" --ssh-config "$RUNTIME/ssh.conf" --client-alias pool-b-client \
  --release "$CONTROL/provenance/juicefs-release.json" \
  --admitter "$TOOLS/experiments/unified-suite/pilot-aliyun/scripts/admit-c1.py" \
  --build-manifest "$CONTROL/provenance/build-manifest.json" \
  --route-key complete_base \
  --mount-spec /srv/ax-client/preparation/parallel-c3-juicefs-01/run/mount/mount.reviewed.json
```

`--route-key`按C1/C2/C3分别选`native_checks`、`complete_route`、`complete_base`；`--mount-spec`必须填该次原始挂载记录，不能借用其他route。B C3实际记录通过code13检查，wrapper SHA256为`0ed35ddd2642ae2f8d57b9083795e3d90f149928cb6476b3f6bbed2108792079`；原始命令、版本输出和结果已存入`evidence/preparation/parallel-client-binary-b-c3-20261111-01/`。
