# pool-a 新计划输入准备

- DB50：`/srv/ax-client/a/parallel-c1-native-01`。1 MiB、4 KiB 两种块大小，各准备64 MiB，并做原挂载和新缓存重挂后的完整CRC检查，六次均通过。
- DB51：`/srv/ax-client/qualification/parallel-native-flat-01`。新BrewFS三次挂载均正常退出，重挂后的修改已再次读回。
- 本次没有正式性能样本，没有模型调用，没有新建云实例。`completion.json`记录输入和版本。
- 原始封闭包202成员、65,950字节，已私网取回并独立校验全部成员；包路径及SHA见`completion.json`。包保留在节点和本机缓存。
- 两次归档检查失败均保留：首次包位置触发路径检查，第二次对journal原文做逐字比较受到JSON键顺序影响。改用独立暂存目录并分别保留journal，未重跑输入准备或资格检查。
