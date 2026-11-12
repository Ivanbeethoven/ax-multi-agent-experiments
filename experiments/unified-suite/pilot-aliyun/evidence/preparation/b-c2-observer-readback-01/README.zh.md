# B 池盘 0：逐文件核对已归档的 C2 副本

[原 C2 的正式关闭与 OSS 读回](../../c2-jfs-01/README.zh.md)保留了正式轮的 objects 包，以及三轮 On 校准嵌在六轮包内的 objects 包。协调机在现存的独立下载包中提取四个清单，核对下载件、外层与内层包的 SHA 和成员。生成的 4,674,910 字节文件为 /srv/ax-pilot-control/preparation/b-c2-observer-readback-01/archive-proof.json，SHA-256 为 b96cc822c8bb99e08c316c00c83611a8d00dbf0f16f2fa47a32c1a1c9cfd5f6e。可用[生成器](../../../scripts/plan-b-c2-observer-proof.py)从原下载包复现。

仅唤醒 B objects，在原物理锁内核五个控制库、保持 Docker 无容器运行。原盘 0 以 ro,noload 私有挂载；[现场回执](copy-readback.json)确认 8 棵目录、70,542 个普通文件全部逐个匹配归档长度及 SHA。四个历史空 socket 不属于数据。校验后卸载，未删任何文件。实例于 [12:02 左右返回 StopCharging](cloud-stopped.json)，六台实验实例均停机。启动请求到确认停机约 101 秒，按原边际费率估计约 ¥0.05，未新增资源。

实测可用 **4,040,151,040 B**；八棵目录实占 **9,027,256,320 B**。若全部回收且无其他写入，预计可用 **13,067,407,360 B**，超过 10 GiB 两池启动探测约 **2.33 GB**，也超过本盘 20% 预留约 **1.05 GB**。这是清理前估算；实际取决于清理后的原生 statvfs。仍明显低于 C1 混合读写单轮的对象盘 0 预算，不能据此派发 C1。

本次只有归档和盘上内容的只读比对，不是清理批准，也不替代本次开机的挂载、租约、空槽、Redis、容量和硬件资格检查。
