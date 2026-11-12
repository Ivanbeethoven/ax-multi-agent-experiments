# 一万文件小试：准备和核对输入

在 Linux 或 WSL 从仓库根目录运行。使用已有的 `smallfiles` 可执行文件，并从它的构建或分发回执取得 SHA-256；脚本不下载、不编译、不挂载。

```bash
P=experiments/unified-suite/pilot-aliyun/scripts
OUT=/absolute/path/to/new-pilot-10k
python3 "$P/prepare-pilot-tenk.py" --out "$OUT" \
  --smallfiles "$SMALLFILES" --smallfiles-sha256 "$SMALLFILES_SHA"
```

| 用途 | 输入目录 | 文件 | 子目录 | 逻辑字节 | 逐文件清单 |
| --- | --- | ---: | ---: | ---: | --- |
| C3 小文件 | `smallfiles-source` | 10,000 | 1,025 | 40,960,000 | `receipts/c3-tree.json` |
| C4 原生派生 | `fork-source` | 10,000 | 10 | 40,960,000 | `receipts/c4-fixture-tree.json` |

C3 使用原 `smallfiles prepare/verify-base` 生成并逐文件核对。C4 使用原 `fs_step.py fixture` 生成，逐路径与按确定性公式独立计算的预期树比较。两个输入的命名、目录和内容不同，不能互换；C4 的 BrewFS 和 JuiceFS 两臂使用同一棵 C4 输入。加 `--smoke-files 7` 可先验证入口，回执标为 `smoke_only=true`。

把 `input-set.json` 和两个清单留在挂载外。导入和冷重挂后按对应清单核验。C4 若记录子工作区首次读取，先单独用 `bench/fs_step.py first-read`，再遍历子树。下面的 `$TREE`、`$TREE_SHA` 使用对应输入的清单路径和 SHA；不要从待测目录生成预期清单。

```bash
python3 bench/fs_step.py check --root "$READBACK" \
  --expected "$TREE" --expected-sha256 "$TREE_SHA" --output "$NEW_RECEIPT"
```

- **C3**：把 `smallfiles-source` 打成新的 Packed lower，挂载后以 `smallfiles verify-base --files 10000` 核全量原始内容。运行写操作后，`smallfiles-oracle.py --files 10000` 可从已验证源和写入状态导出全树预期。旧 100k 挂载入口绑定旧清单和文件数，不能直接调用。
- **C4**：把同一 `fork-source` 发布成 BrewFS 原生 fork 所需的不可变版本，并导入 JuiceFS 父目录供原生 clone 使用。独立子树预期用 `fork-oracle.py --files 10000` 生成，参数为父树清单及其 SHA、`receipts/c4-fixture.json` 及其 SHA 和子 ID。现有 100k 草稿入口仍固定其旧输入；1 万文件尚无实际挂载、派生、冷验和关闭回执，优化 BrewFS 产物仍缺。

[本机生成回执](../pilot-aliyun/evidence/progress/closing-small-pilot-20261112/local-inputs/input-set.json) · [C3 全树复核](../pilot-aliyun/evidence/progress/closing-small-pilot-20261112/local-inputs/c3-check.json) · [C4 全树复核](../pilot-aliyun/evidence/progress/closing-small-pilot-20261112/local-inputs/c4-check.json)。两个 10,000 文件 oracle 均接受了输入；7 文件小样还验证了 C4 六种子文件修改，改错一个字节会被拒绝。这些是本机输入资格，未开云机或测文件系统性能。
