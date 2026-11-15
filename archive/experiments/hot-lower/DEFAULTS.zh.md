# 默认缓存配置补充

2026-11-13 完成，本地检查通过。BrewFS 提交
[`579ee04`](https://github.com/brewfs/brewfs/commit/579ee04a047e153ffb5ed6179174194be9172644)。

本次补充将热 lower 测试中手动配置的 8 MiB 帧缓存设为 workspace Packed-v3 挂载默认值。
原性能测量对应 `02d4b83`，构建时记录为 `ca07159` 加完整补丁；这些源码和二进制 SHA 保持原样。

| 缓存 | 默认值 | 调整方法 |
|---|---|---|
| 上层行缓存，含空区间/空目录结果 | 默认开启，共享 65,536 项上限，TTL 60 秒 | `BREWFS_WORKSPACE_ROW_CACHE_TTL_MS` 调 TTL；`BREWFS_WORKSPACE_EMPTY_QUERY_CACHE=0` 关闭空结果缓存 |
| 已认证 lower 帧 | 每挂载 8 MiB，按需填充 | `BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES`；0 关闭，非零为 513 B–64 MiB |

帧缓存计入实际内容和每项 512 B 的管理开销，超过单项容量的帧直接读取而不保留。
现有 VFS 和 Packed 元数据缓存预算没有增加。上层仍按租约、视图和修改失效。

当前上层修改仍等待远端元数据库提交成功。半异步方案也必须把全部属性、目录项、
删除标记和数据映射写回远端；只调整何时返回与如何合批，本地缓存不替代元数据库。

启用帧缓存时，每次重新从后端取得帧都验证摘要，不需要额外设置
`BREWFS_PACKED_VERIFY_MEMO=0`。新增测试在缓存失效后注入同长度损坏内容，要求读取
报错；随后恢复正确内容，验证失败的填充没有污染缓存。另检查默认容量、关闭及非法输入。

补充验证不重新计算旧性能表，也不把旧二进制标为新版本。新增日志与源码记录放在
`evidence/defaults-01/`。首次编译因 WSL 内存压力中断，没有执行到测试；恢复 WSL 并
准备 8 GiB 临时 swap 后，以单编译任务重跑完整检查。临时 swap 仅用于编译，未用于性能测量。

| 检查 | 结果 |
|---|---|
| 默认 workspace，lib + bins | 1,355 通过，265 原有 ignored |
| 完整 Overlay 库 | 2,790 通过，485 原有 ignored |
| 默认及 Overlay 的 Clippy，all-targets，警告作为错误 | 通过 |
| fmt、workspace check/build、两种 FUSE runtime、shell 检查 | 通过 |
| 临时 swap | 已停用并删除 |

[机器可读记录](evidence/defaults-01/record.json)、[完整日志](evidence/defaults-01/check.log)、
[本次补丁](evidence/defaults-01/source.patch)。重复修改同一对象的本地待提交视图、合批和
版本确认规则已写入 BrewFS 同步设计，尚未实现；当前元数据提交仍等待远端确认。
