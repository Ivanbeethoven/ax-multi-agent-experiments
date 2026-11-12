# C4 历史 On1：OSS 归档

11 月 12 日 12:14–12:22，北京时间。三个原角色包在协调机完成凭据扫描后走杭州 OSS 内网上传，禁止覆盖，并核对完整读回与匿名公网 HEAD。随后协调机从 OSS 匿名下载独立副本，逐个校验全部归档成员的大小和 SHA-256。

| 角色 | 压缩包字节 | 成员数 | 下载 | 上传回执 |
| --- | ---: | ---: | --- | --- |
| client | 59,831,853 | 85,741 | [client.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c4-a-flat-on1-historical-01/client.tar.gz) | [回执](client.tar.gz.json) |
| metadata | 39,128,425 | 86,057 | [metadata.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c4-a-flat-on1-historical-01/metadata.tar.gz) | [回执](metadata.tar.gz.json) |
| objects | 644,436,975 | 18 | [objects.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c4-a-flat-on1-historical-01/objects.tar.gz) | [回执](objects.tar.gz.json) |

共 **743,397,253 字节、171,816 个归档成员**。objects 的 18 个成员包含原折叠 NDJSON，覆盖 2,954 个样本，并非只有 18 个样本。 [独立核验](independent-readback.json)固定恢复后 finished、原归档索引和各包 SHA；原测量、失败回执、归档均未变。

[发布守卫](held.json)在 5 个控制库中确认无租约，覆盖 40 把原调度锁，独立验证后[释放](guard-result.json)。[阿里云状态](cloud-status.json)确认六台实验机 StopCharging，原协调机运行；本轮未唤醒任何实验机、未运行模型。[状态库读回](control-status.json)仍有 14 项正式完成。On1 仍只计入历史采集开销校准，不增加正式数。保留的两个 Python 文件是本次协调机用过的守卫与独立读回代码。
