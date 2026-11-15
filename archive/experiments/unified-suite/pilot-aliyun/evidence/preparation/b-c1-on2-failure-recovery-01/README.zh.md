# B 组 C1 混合读写：On2 失败及收尾

2026-11-11 20:55:47（北京时间），原协调服务 `ax-pilot-b-c1-mixed-after-c2-01.service` 的 invocation `007fcbaf78044d93b04d924e885491df` 退出 1。事后 `systemctl show` 的 `ExecMainStatus=0` 来自 `--collect` 后已回收的服务，不能代替原 journal。

Off1、On1 完成。On2 的 fio 退出 0（1225.664183008 秒）；20:37:30 后没有对象节点采样，`application-finished` 的原对象节点协议返回 `ConnectionRefusedError [Errno 111]`。对象盘 0 在连续样本 20/22/24/26/28 的空闲字节为 1,031,434,240 / 774,340,608 / 544,800,768 / 291,758,080 / 58,949,632。后续一度四盘 `df` 可用空间为 0；复查空间有回升。对象采集器日志为空，没有直接的 ENOSPC 异常栈。On2 不进入性能结果；余三轮及正式 C1 未启动。

21:06:59，使用原工具在无 B 运行单元、租约和 fio 时结束残留：两次 native 上传债务样本间隔 1.054 秒，均为 0 字节、0 操作；原 JuiceFS 正常退出并卸载；客户端、元数据采集器正常结束（382/383 样本）；对象采集器已退出，没有补记它的退出回执。三节点 `sync` 退出 0，随后确认原槽位、进程、FUSE 挂载均空。未执行冷读、新测量、扩盘、删除或云实例停机。

协调者归档：`/srv/ax-pilot-control/preparation/b-c1-on2-failure-recovery-01/`。`result.json` SHA256 `d535b968fba96142258b12bf4ade40912c8518904f5b6ef87ec53f7f7fc96184`。小证据副本目录：`C:/Users/HXY/.cache/ax-pilot-20261111/b-c1-on2-readonly-2100-01/`，SHA256 见 [sources.json](sources.json)。

[recover-on2.py](recover-on2.py) 保留本次执行的源字节，限定原失败 invocation、节点工具和输出。由已配置的跳板 SSH 将源送入协调者 `code-15` 下的 `tr -d '\r' | /usr/bin/python3 -B -`。该文件仅供审计，不能重跑；已成功收尾后，需要刷新凭据时只读采集原 `08-postcheck-*/intent.json` 的探针命令，写新目录。
