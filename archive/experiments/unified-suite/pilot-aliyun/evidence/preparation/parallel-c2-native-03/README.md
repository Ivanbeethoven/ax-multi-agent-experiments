# C2 原生路线资格检查

本次通过的是功能与数据完整性检查；正式性能单元另行记录。

- 输入：`/srv/ax-client/trace-exact-02/cases/pytest-full-02`；完整清单 `/srv/ax-client/trace-exact-02/manifest-replay-inputs-03.json`，SHA `71a0661b82a433e9d14da29287a2bc1860460015d4887031f90a03ff5451a021`，共 67,577 个文件。
- BrewFS：`ca07159`，独立二进制目录 `/srv/ax-client/build-ca07159-01`，Redis DB54，新资产 `/srv/ax-client/a/parallel-c2-native-03`。
- 回放程序：`/srv/ax-client/assets/bin/full-fuse-replay-parent-fd-01`，SHA `35c45abd910d826ea17b5d6dfc750c7083cda5e8ce8f3bbe5ef77a78ffbea1aa`。使用 `--portable-terminal-probe --cache-parent-fds`，资格、校准、正式测试保持同一路径策略。
- 原始资格输出：`/srv/ax-client/qualification/parallel-c2-native-03`；五字节参数/读内容预检位于相邻的 `parallel-c2-native-03-replay-preflight`，通过后才创建资产。
- 协调者资格输出：`/srv/ax-pilot-control/parallel-control-01/qualification/pool-a-c2-01`。协调者重新核对了完整输入清单。

2026-11-11 05:44:20 UTC 完成：1,747,109 次回调、281,774,624 字节读、194,706,661 字节写均与原记录相同；中间读取逐次核对字节，最终文件内容、权限、链接关系和复制后的 SQLite 完整性通过，原后端卸载后冷重开再次通过。两次挂载均正常退出。1,048 次终端查询返回码采用预先声明的兼容规则，未减少回调或读取字节。

保留的前两次失败不计为性能结果：`-01` 在导入后发现旧本地源的 112 项权限不匹配，尚未回放；`-02` 的旧程序不支持参数，在解析时退出，尚未执行任何回放回调。两次均正常停止挂载，原数据和错误保留。`-03` 使用此前已有的准确输入及支持所需参数的已存二进制，没有修改产品或原始 trace。

`latest-branches.json` 保存实际分支查询的命令、退出码和时间；查询发生在已授权资格启动后，正式批次开始前，两条分支仍与冻结二进制相同。`local-tests.json` 记录已完成的 17 项 Python 与 7 项 Rust 检查；后续没有重复编译或本地全量扫描。
