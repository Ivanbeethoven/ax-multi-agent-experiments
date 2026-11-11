# 校准归档路径修复

首次实际归档在`native-randwrite-01/cal-n-rw-off1`停止。原工具把`/srv/ax-client`作为打包根目录，又把包写进该根目录；通用归档器拒绝了此路径。见[协调者错误](retention-first-controller.stderr)和[节点错误](retention-first-worker.stderr)。本次只归档既有数据，没有重跑校准或正式负载。

修订后先把明确列出的原始文件复制到新归档目录的`raw/`，再把包写在其外层。复制前后仍核原目录的全部文件列表、大小和SHA，缓存与已关闭的IPC socket继续单列。新增检查使用真实的“同一数据盘内部归档”目录关系；[检查通过](regression-checks.json)。

新工具仅放到协调者`/srv/ax-pilot-control/code-09/`，SHA256为`bb7ac8a647b61ef570bd8ced09ec3461925614407f672345d4febaa50097a8f4`。原code-08和首次失败目录保留。新输出`retained-calibrations/native-randwrite-02`已完成全部六次原始校准归档，独立副本核了12个节点包的全部成员，见[实际结果](../../cloud-20261111/7084af367e0bcfad-r1-native/calibration-nested-check.independent.json)。

`verify-retained-bundle.py`进一步验证包内嵌套包。检查覆盖外层SHA正确但内层包损坏的情况，损坏包不能生成成功记录，见[检查结果](nested-checks.json)。
