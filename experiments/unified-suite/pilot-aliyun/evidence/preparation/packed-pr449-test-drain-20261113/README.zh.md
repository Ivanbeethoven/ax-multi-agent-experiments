[Packed PR #449](https://github.com/brewfs/brewfs/pull/449) 的后续测试修复已推送至 `53c725f36db5bcd060ec2483a0abecb943bed4ea`。相对性能实测版本 `7093f141`，只修改 `src/chunk/store/read_amplification_tests.rs`，该模块由 `cfg(test)` 排除在 release 之外；Cargo、构建脚本和所有生产源码不变。[原性能结果](../packed-cache-7093-20261112/README.zh.md)仍绑定原二进制及其 SHA，不改写为新提交的测量。

原测试文件在主线 `ca07159` 与 `7093f141` 中完全相同。其等待函数把“已调度预取数量连续 20 ms 不变”当成完成；生产实现还有 8 槽 `try_acquire`，忙时会丢弃推测读取。原 GitHub 失败只记录 full GET 为 28、预期为 64，没有丢弃计数，不能把差额全部归为未完成或全部归为丢弃。

修复将此流量性质测试明确为逐次串行冷读：每次等待真实 full GET 响应完成，再等待生产 semaphore 的全部许可归还，确认缓存发布结束后才继续。原五个测试的内容、请求数、读放大和预算断言均保留。新增真实 GET 门闩回归要求等待函数在后台被阻塞时保持 pending，放行后检查完整数据已进入缓存；没有提高原验收阈值。

| 记录 | 结果与解释 |
| --- | --- |
| [原 PR CI](https://github.com/brewfs/brewfs/actions/runs/43298840765) | `7093f141`：1,353 库测试通过、1 失败、265 忽略；失败为 28/64 请求断言。 |
| 首轮新夹具 | 两个分支均使用 64 KiB 对象，等于页大小，读路径直接合并页缓存而未触发后台 GET；均在入口等待超时。两个日志保留，均不算有效 RED/GREEN。 |
| [有效 RED](https://github.com/brewfs/brewfs/actions/runs/43305784066) | `2a1ed7fa`：同一个 4 MiB 门闩回归搭配旧等待函数，精确 1 项测试在 0.06 秒按“后台尚未完成却提前返回”断言失败。 |
| [有效 GREEN](https://github.com/brewfs/brewfs/actions/runs/43305771564) | `53c725f3`：完整构建、1,355 库测试通过／265 忽略、严格 `clippy --workspace --all-targets -- -D warnings` 全通过。 |

两支最终门闩回归逐字相同。另有 9 项本地轻量检查通过：格式、4 个 shell 语法检查、3 个脚本回归与差异检查。GitHub 执行原默认 feature workflow；这不是新的一轮工作区 feature 性能测量。

更新 PR 后，[pull_request 触发的 CI](https://github.com/brewfs/brewfs/actions/runs/43308374335)也在同一 `53c725f3` 头完成：构建、1,355 库测试／265 忽略及严格 Clippy 全通过。[独立复核](pr-triggered-ci-review.json)和 [6 成员增补归档](pr-triggered-ci.tar.gz)单独保存；协调者再次核对全部成员、原始通过数及远端 run/head。[增补回执](pr-triggered-ci-receipt.json)记录 SHA-256 `7292d88ed6ee9e35aff551176229ec0e8a25425a80386a6c201c0f10adccd27d`，原 48 成员包保持不变。

[qualification-final.json](qualification-final.json)记录提交、命令范围和各门结果。[evidence.tar.gz](evidence.tar.gz)含全部原始日志、两次夹具、补丁、源码和复核回执，SHA-256 为 `3861fe4b73a80f4ced74a3553ab8fab8c77cf52e3a84971287c5ed79331b2146`。协调者[复核](coordinator-review.json)确认全部 48 个成员、47 个载荷哈希和清单的外部哈希，并直接从日志核对 RED/GREEN 结果。没有归档构建目录、二进制或凭据。
