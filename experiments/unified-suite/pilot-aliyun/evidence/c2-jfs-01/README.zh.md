# B 组 C2 JuiceFS：已闭合并公开归档

原正式单元 `05357b5089cd679c-r1-juicefs` 的关闭回执为 `outcome: complete`。2026-11-11 23:54–23:55（北京时间），从原协调节点依次发布三个角色包和原六轮校准包，共 **139,424,013 B**。每包均通过凭据扫描、原 SHA 核对、禁止覆盖上传、内网完整读回及匿名公网 HEAD。

随后协调节点从 OSS 无签名下载新副本，用固定 `verify-retained-bundle.py` 核对外层和全部嵌套归档成员：

| 包 | 字节 | 核验成员数 | 公开下载 |
| --- | ---: | ---: | --- |
| client | 9,379,100 | 5,825 | [client.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c2-05357b5089cd679c-r1-juicefs/client.tar.gz) |
| metadata | 2,564,152 | 5,781 | [metadata.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c2-05357b5089cd679c-r1-juicefs/metadata.tar.gz) |
| objects | 20,369,828 | 9,014 | [objects.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c2-05357b5089cd679c-r1-juicefs/objects.tar.gz) |
| calibrations | 107,110,933 | 61,514 | [calibrations.tar.gz](https://brewfs-agent-traces-20261023-4ccdae73.oss-cn-hangzhou.aliyuncs.com/pilot-results/20261111/c2-05357b5089cd679c-r1-juicefs/calibrations.tar.gz) |

校准包包含外层及 12 个嵌套包，全部通过。上传和独立读回期间，SSH stdin 守卫持续持有两池、五个控制目录对应的 **40 把现有调度锁**；前后全部租约为空，原运行单元已终止。23:55:46 协调节点写下锁释放回执。操作只使用协调节点，未启动工作节点或新增测量。

证据：[原关闭回执](publication-20261111-01/original-close.json)、[本次发布结果](publication-20261111-01/result.json)、[独立读回](publication-20261111-01/independent-readback.json)、[守卫释放](publication-20261111-01/coordinator-guard/result.json)、[全部小回执 SHA](publication-20261111-01/source-files.json)。原回执保持不变。

首次守卫预检在上传前因两个既有锁目录指向 canonical 目录而拒绝；确认四对象仍为 404 后，在新目录按实际锁路径去重取得守卫。该次拒绝及 HEAD 回执保存在 [guard-01-rejected](publication-20261111-01/guard-01-rejected/guard-held.json)。
