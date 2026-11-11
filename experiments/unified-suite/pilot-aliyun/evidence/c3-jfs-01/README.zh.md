# C3 JuiceFS：10万文件

本次正式样本已正常完成，独立检查接受。只是一轮、一个执行槽位；其余方法尚未组成完整对比。

- `formal/sample.json`：8阶段性能指标及原始证据引用。
- `formal/closing.json`：正常停止、原生上传债务和归档引用。
- `independent-close-01/result.json`：重新核验3角色归档、采样完整性、正式样本、原进程退出、挂载消失和执行槽位为空。
- `historical-six/`：原6次采集开关校准的保留结果；没有重跑。综合包20,017,178字节，留在协调者。
- `original-attempt/`：旧命令入口拒绝`create-w01`的失败及收尾。该次没有执行负载，不计性能样本。
- `new-control/`和`runner-patch/`：新批次、完整条件一致的证明，以及只修改命令入口的差异。原失败没有重置。
- `state-snapshot.json`：交接时3个状态库的实际状态，不含租约令牌。
- `MANIFEST.json`：104个原文件的路径、字节数和SHA256。部分本地路径为适应Windows缩短，`logical_source_name`保留原名称。

3角色正式归档共15,337,732字节、13,020个文件成员，均已核验全部内容SHA256。大包、完整原始采样和完整保留清单留云；`remote-retention.json`记录实际路径和SHA256。本目录仅约253KB的原始小证据。

07:20:05Z已将B池交给C4，实际交接见`new-control/handoff-b-c3-to-c4-01.json`；Redis、RustFS及原采集汇聚服务保持运行。
