# 固定的 16 个真实 Agent 任务

这里已经冻结任务身份、原始代码版本、问题文字和统一提示词。**镜像下载、环境启动、参考补丁评分均未执行，不能据此写成“16 个任务已可运行”。** `live-tasks.json` 中的镜像仍是数据集提供的 tag，`image_digest` 为空，运行资格为 `not_run`。

## 任务怎样选

固定四个仓库：`django/django`、`pytest-dev/pytest`、`scikit-learn/scikit-learn`、`sympy/sympy`。在每个仓库内，将完整数据集中的 `instance_id` 按区分大小写的字符串字典序排序，取前四项，再按上述仓库顺序拼接。这里是**字符串排序**，因此 pytest 的 `10051` 排在 `5262` 前；没有按编号转整数排序。

选择过程不读取 Agent 成功率、请求数量、运行时间或 BrewFS 性能。这是四个 Python 仓库的固定案例组，不代表全部 SWE-bench 任务。仓库相同不代表代码版本相同：16 项必须各自使用其原始 `base_commit`，不能为了提高共享量改成同一个版本。

| 序号 | 仓库 | 任务编号 | 原始代码版本（前 13 位） |
|---|---|---|---|
| 1 | Django | 10097 | `b9cf764be62e7` |
| 2 | Django | 10554 | `14d026cccb144` |
| 3 | Django | 10880 | `838e432e3e551` |
| 4 | Django | 10914 | `e7fd69d051eaa` |
| 5 | Pytest | 10051 | `aa55975c7d3f6` |
| 6 | Pytest | 10081 | `da9a2b584eb7a` |
| 7 | Pytest | 10356 | `3c1534944cbd3` |
| 8 | Pytest | 5262 | `58e6a09db49f3` |
| 9 | Scikit-learn | 10297 | `b90661d6a46aa` |
| 10 | Scikit-learn | 10844 | `97523985b39ec` |
| 11 | Scikit-learn | 10908 | `67d06b18c68ee` |
| 12 | Scikit-learn | 11310 | `553b5fb8f84ba` |
| 13 | SymPy | 11618 | `360290c4c401e` |
| 14 | SymPy | 12096 | `d7c3045115693` |
| 15 | SymPy | 12419 | `479939f8c65c8` |
| 16 | SymPy | 12481 | `c807dfe756969` |

完整 ID、40 位 commit、环境准备 commit、题目原文、完整提示词及其 SHA-256 都在 [live-tasks.json](live-tasks.json)。同一任务在不同存储系统和并发度下使用完全相同的这些字段。提示词只要求正常修复与测试，不要求为了制造 I/O 而扫描所有文件或延长工作时间。

## 文件分别给谁

| 文件 | 用途 | Agent 能否读取 |
|---|---|---|
| `live-tasks.json` | 调度器取任务、提示词、版本和评分测试标识 | 不挂入 Agent；只提取当前任务的 `prompt` |
| `grading/<instance_id>/test.patch` | 数据集的测试补丁 | 否 |
| `grading/<instance_id>/eval.sh` | 数据集原有的评分脚本 | 否 |
| `grading/<instance_id>/oracle.json` | 测试列表、解析器、脚本校验值 | 否 |
| `grading/index.json` | 评分材料的来源和目录 | 否 |
| `freeze_live_tasks.py` | 重新从固定 Parquet 生成以上文件 | 控制端使用 |

Agent 只得到该任务的原始代码、`prompt` 和固定执行环境。`FAIL_TO_PASS` / `PASS_TO_PASS` 保留在控制端清单中便于核对，不注入提示词；完整数据集、评分目录和标准修复补丁均不挂入 Agent 工作区。没有导出任何标准修复补丁，只保存其 SHA-256，以便后续在隔离的评分环境核对来源。

原始 `eval.sh` 已包含应用测试补丁的步骤，不能先应用 `test.patch` 后再无条件执行同一脚本。脚本只存档，尚未运行；后续应由固定版本的评分程序在一次性副本中执行，不能在原始代码快照或 Agent 的活跃目录里直接执行。

## 三种实验不要混为一组

1. **16 个独立任务并发**：总共就是此处 16 个不同问题，分别在并发度 1、4、16 下完成。同一轮总任务数和顺序固定，不能用 1 个任务的串行时间对比 16 个任务的并行时间。输出是 16 份补丁、工具日志、模型日志和独立评分结果。
2. **同任务多个候选**：另建执行清单，对一个固定 `instance_id` 启动 16 个独立候选，使用相同初始快照和各自私有写层。这回答共享基线与候选隔离问题，不能把候选数当成 16 个不同任务。改变并发度时仍保持 16 次候选尝试。
3. **一个生产者、多个消费者**：另建输入与检查规则，明确生产者生成什么文件、何时发布版本、消费者读取什么及应输出什么。这里的 16 个独立修复任务本身不构成依赖关系，也不能证明生产者到消费者的数据共享收益。

真实 Agent 的程序退出码不能代替修复成功。评分以固定程序解析补丁应用后的测试结果：要求修复测试通过，原有应通过测试仍通过。超时、空补丁、环境失败和评分失败均分别保留，不能从批次完成时间或成功率分母里静默删除。

若需要解释 Packed 或私有增量对工具时间的影响，可以在这些相同快照上另建固定命令流程（搜索、读文件、应用控制端固定补丁、测试）。其输出可精确复查，但必须称为固定工具流程，不能称为“真实 Agent 轨迹重放”。历史 FUSE 记录缺少完整路径、载荷和同步关系，不能直接恢复该流程。

## 冻结来源与复现

- 数据集：[SWE-bench/SWE-bench_Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified)，`test`，500 条。
- 本地数据集仓库 commit：`78f471bf655a3137b2e8a75af1501690ec009ec3`。
- Parquet：6,304,616 bytes；SHA-256：`030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25`，与该 commit 的 Git LFS 指针一致。
- 本地来源：`/home/hxy/research/agent-fuse-swebench/verified.parquet`，Windows 可通过 `//wsl.localhost/Ubuntu-24.04/home/hxy/research/agent-fuse-swebench/verified.parquet` 读取。
- 本地 SWE-bench 评分代码 commit：`02e7a74ffd0b707aab73d203fe87bdc7c76afc8e`；这里只记录已发现版本，尚未验证其与 16 个任务镜像的兼容性。

在本目录运行以下命令，可重新生成相同任务及评分材料。脚本仅读取数据并写文件，不启动容器、模型、网络请求或实验。

```bash
/home/hxy/research/agent-fuse-swebench/.venv/bin/python freeze_live_tasks.py \
  --source /home/hxy/research/agent-fuse-swebench/verified.parquet
```

正式比较前，需锁定镜像 digest，并在隔离评分环境核对原始版本和标准修复后的测试表现；控制端读取标准修复时须核验这里记录的 hash。若镜像或评分不可用，记录具体原因并在采集性能结果前修改冻结协议。Agent 没修好、运行慢或某个系统收益小都不是换题理由。
