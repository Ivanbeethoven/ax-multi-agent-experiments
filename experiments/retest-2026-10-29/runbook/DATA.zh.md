# 输入交接：27份trace，Drive与OSS镜像

用户已交付[备份目录](https://drive.google.com/drive/folders/1TsB-GoxNHMhdn-IWTE5S0nQCMeJwro9X)、[下载说明](https://drive.google.com/file/d/1i_6xKmkFgc79M5PSmU_Vp0MDwG8Nfazn/view)、[旧完整C1 prompt](https://drive.google.com/file/d/1qu8EyZrktMDFHjpuFJslQqfPAARMEFhn/view)。数据为**20份扩展＋7份历史trace，约13.65 GB**，本地和OSS原件保留。27是采集归档数，不是27个独立benchmark。

本轮已核对本地MANIFEST与下载校验工具的独立SHA。未重新下载或复验Drive全部数据。MANIFEST含136条文件记录，自身另以独立SHA校验；交接所述137个文件的计数口径由下载方结合TRANSFER_COMPLETE对账，不增造清单条目。handoff文件在数据上传后另行发布，不伪称包含在原数据MANIFEST中。

## 1. 哪些数据进入哪些实验

| 用途 | 选择 | 边界 |
|---|---|---|
| C1 pilot | pytest-full-02完整包，407606955 B，约408 MB | 不需全量下载；N=1/8/16，共6 cohort |
| C1正式主线 | 原7份历史trace：三份扩展负载＋四份单Agent覆盖，见[C1方案](../C1-ALIYUN.zh.md) | 27份可用不自动改变预先指定矩阵 |
| C1独立上传消融 | django、ripgrep-20261026、archive-io-20261026 | study=writeback，独立186 cohort；不与baseline的132+54混淆 |
| 20份扩展trace | API-Bank、AppWorld、BFCL、DABench、dbt、ML、Office、terminal任务的采集 | 用TRACE-CATALOG逐项核benchmark/任务/模型/采集成功与可回放性；若加入测量，另冻结输入矩阵，不补跑原模型任务 |
| C2 | 冻结真实ML corpus与访问读集 | ML任务trace本身不等于8M/32M真实独立小文件已备齐 |
| C3 | 固定seed/对象manifest生成的对象池 | 不消费27份trace；4KiB Range内容验收独立 |
| C4 | F的1M/400GiB语料；M的明确checkpoint及会话/受控trace | 普通文件访问trace不自动提供可恢复的Agent会话或工具边界 |

Drive中旧C1 prompt的**数据路径和摘要仍有效**；旧硬件和计划数量以当前冻结仓库的[新版部署方案](DEPLOYMENT.zh.md)及[C1方案](../C1-ALIYUN.zh.md)为准。不会覆盖已上传交接原件。

## 2. 可信身份

| 文件 | SHA256 |
|---|---|
| MANIFEST.json | `0d6c10fbfc2fddbd98114de1f60d019b829a822ebeabd925fa88bfcbb691f3be` |
| TRACE-CATALOG.json | `f41fc2faa2137881ae6ff249701465436a0ddb9e506591560481e44fb9e2e5a4` |
| handoff/verify_download.py | `ee8b3a17d5f0f2d9cc0188e1f5a69553e37bba21840f14f203eb88d938a2e1ef` |
| 分卷目录内restore_archive.py | `142c1d8207b931d07fef39185f64b93612794f8216bf72fbe5ba2841aaa677e3` |

先核MANIFEST与helper的独立pin，再运行helper；不能只让下载来的脚本验证自己。历史7份还与Git中[archives.json](../../../scenarios/k8s-containers/archives.json)交叉核对。上游路径、包顶层root_dir和本轮逻辑case ID分开记录。

## 3. 新Linux环境按需下载

rclone使用执行方自己的有读取权限账号；只读scope，凭据不进入Git、prompt、日志或结果。只在remote不存在时创建，已有remote先核对；首次授权按[rclone官方说明](https://rclone.org/remote_setup/)完成。

```bash
rclone config create c1data drive scope drive.readonly root_folder_id 1TsB-GoxNHMhdn-IWTE5S0nQCMeJwro9X --no-output
```

以下由执行方设置自己的绝对路径DATA_ROOT，并预先创建对应目录。每条命令单独执行和检查，不在计时中下载：

```bash
rclone copyto c1data:MANIFEST.json "$DATA_ROOT/MANIFEST.json" --checksum --immutable
```

```bash
rclone copyto c1data:TRACE-CATALOG.json "$DATA_ROOT/TRACE-CATALOG.json" --checksum --immutable
```

```bash
rclone copyto c1data:handoff/verify_download.py "$DATA_ROOT/handoff/verify_download.py" --checksum --immutable
```

```bash
printf '%s  %s\n' '0d6c10fbfc2fddbd98114de1f60d019b829a822ebeabd925fa88bfcbb691f3be' "$DATA_ROOT/MANIFEST.json" | sha256sum -c -
```

```bash
printf '%s  %s\n' 'ee8b3a17d5f0f2d9cc0188e1f5a69553e37bba21840f14f203eb88d938a2e1ef' "$DATA_ROOT/handoff/verify_download.py" | sha256sum -c -
```

只取pilot：

```bash
rclone copyto c1data:traces/historical-oss/pytest-full-02.tar.gz "$DATA_ROOT/traces/historical-oss/pytest-full-02.tar.gz" --checksum --immutable
```

```bash
python3 "$DATA_ROOT/handoff/verify_download.py" --root "$DATA_ROOT" --manifest-sha256 0d6c10fbfc2fddbd98114de1f60d019b829a822ebeabd925fa88bfcbb691f3be --file traces/historical-oss/pytest-full-02.tar.gz --output "$DATA_ROOT/pilot-download-verification.json"
```

pilot包SHA为 `9953d90517eaf520814150f80abccb11f9d74b7ac84ecc56fa394a5f8495aa3d`，随后仍需[归档检查](../../../scenarios/k8s-containers/scripts/check_archives.py)、解包后[trace检查](../../../bench/trace_checks.py)及本机语义资格。

需要全部输入时才执行：

```bash
rclone copy c1data: "$DATA_ROOT" --checksum --immutable --transfers 4
```

```bash
python3 "$DATA_ROOT/handoff/verify_download.py" --root "$DATA_ROOT" --manifest-sha256 0d6c10fbfc2fddbd98114de1f60d019b829a822ebeabd925fa88bfcbb691f3be --all --output "$DATA_ROOT/full-download-verification.json"
```

## 4. 历史大包的9分卷

archive-I/O目录为 `traces/historical-oss/deepseek-opencode-20261026/io-compatible.parts/`。整目录下载，先按MANIFEST逐卷核验及helper pin，再执行目录里的restore_archive.py，参数是新输出文件的绝对路径；工具拒绝覆盖。不得按未验证的目录排序直接拼接。

恢复结果必须为 **4752420807 B**，SHA256 **`a4ca1c9e50c7c6cae9bf7f29e2d67cd88777e8fba3934ed5fbb646cf0ce77fb2`**。还原的是原压缩字节，不重压缩、不裁剪事件。OSS原单包同SHA可替代。

输入只在协调/输入节点下载一次，计时外核验并经私网分发；events/blobs放被测挂载之外，每宿主只读共享。清单中的全部blobs、初末树、硬链接、应用DB/WAL/SHM保留。下载完整、采集完成、任务通过、回放正确是不同证据，不相互替代。
