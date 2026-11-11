# 六次校准的原始数据

`retain-cell.py`保留正式单元。六次观测开关校准还需单独保留各节点的原始文件，不能只留下开销汇总值。

在协调者执行`retain-calibrations.py`，每次处理一个已完成单元。使用该单元原来的请求和运行结果摘要：

```bash
python3 "$CODE/experiments/unified-suite/pilot-aliyun/scripts/retain-calibrations.py" \
  --request "$REQUEST" --request-sha256 "$REQUEST_SHA256" \
  --completed-sha256 "$RESULT_SHA256" --unit "$ORIGINAL_UNIT" \
  --out "$CONTROL/retained-calibrations/$BUNDLE"
```

变量均来自本轮实际记录：`CODE`为新部署的代码目录；`REQUEST`为该单元的请求；`RESULT_SHA256`为请求中`out/result.json`的SHA256；`ORIGINAL_UNIT`为原服务名，去掉`.service`；`CONTROL`为唯一状态库所在目录；`BUNDLE`用新的小写字母、数字和连字符名称。

脚本要求原服务已退出、正式单元关闭、无预留单元、Agent槽位为空，并重新核对原挂载、工作进程和采集器进程已结束。尚有测量时不能归档。后台对象服务继续运行，不重启共享采集容器。

保留内容：

- 六次原始协调记录、合同及实际执行脚本。
- 每次客户端的负载输出、I/O记录、两次零上传债务、冷读回和三次挂载退出记录。
- 三次开启采集时，客户端、Redis节点、对象节点的全部原始采样及阶段标记。
- 每个包的外层SHA256和逐文件清单。缓存和已关闭的通信socket明确列为排除项；遇到证据链接、缺目录或文件变化便停止。

输出为`$CONTROL/retained-calibrations/$BUNDLE.tar.gz`及同名`-archive-check.json`。原校准目录不修改，失败不重试。另将两份文件下载到本机，再核外层摘要和全部成员；这一步通过前不能释放机器。归档成功不代替原有性能、开销和正确性验收。

当前状态：六项本机检查通过；尚未在云上执行完整六次校准归档。C1随机写仍在测量，部署新的归档工具不会替换其运行代码。
