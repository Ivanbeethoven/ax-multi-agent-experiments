# 现有pool-a下一阶段准备

2026-11-11完成。没有运行正式性能单元、模型请求或新建资源；原状态库保持完成7、失败1、待执行11、lease为0。

- `code-10`已安装到协调者、client、metadata、objects的新目录，140个成员逐项核验，两次检查均通过。旧code和原状态不变。包SHA256为`0e05e44d19556868107b2274a71043db53d5fbe1a022a98233e0a915f5cf790a`。
- 新BrewFS产物安装到`/srv/ax-client/build-ca07159-01`，9个包成员安装前后均核验，4个程序ldd通过。新BrewFS二进制SHA256为`814499855a949cad7a8752c04f272fa1b02524b36b7ea77b0e4b3bcc6031c5c0`。旧`assets/bin/brewfs`仍为`98e3c0402b735830efab7c80e50d8353625b3f8cecc5343ee15c37a6a761a526`。
- 云上输入在`/srv/ax-client/inputs/pilot-100k-01`，C3与C4各100,000个4KiB文件，每份409,600,000B。原百万输入保留，没有重复下载或回放C2。
- C3生成73.412s、完整原核验0.903s，独立Rust核验器又检查全部内容、名称、模式、owner和链接，退出0。C4生成78.207s、完整树清单8.683s，树SHA256与本机一致：`02aeda272edbab9512595f7b968d840a9cfdc0e8302ed036399bf4151f08bd13`。
- 唯一准备单元`ax-pilot-inputs-100k-01`已成功结束。一次自编收尾检查误把systemd回收后的ExecMainCode=0判为异常；原失败保留，随后核对唯一journal invocation及成功退出消息。原准备没有重跑，现有`close-cloud-inputs.py`核验通过。

正式测量还需新计划、实际inventory/build/input绑定、路线资格、容量及监控开销检查。本目录只证明准备完成。

原始记录在协调者`/srv/ax-pilot-control/preparation/pool-a-next-01/`、client`/srv/ax-client/preparation/pool-a-next-01/`。独立代码/二进制包也保存在本机`C:/Users/HXY/.cache/ax-pilot-20261111/pool-a-next-01/`。本目录只提交小回执、原始命令和journal，不包含二进制或输入文件。
