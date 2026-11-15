# 历史实验材料

当前实验见 [experiments](../experiments/README.md)。本目录按主题保存已有计划、性能结果、诊断和当时的执行脚本，原始运行数据保留原义。

| 内容 | 目录 |
|---|---|
| 六机十万文件测试 | [idcai-pilot](experiments/idcai-pilot/README.md) |
| 统一套件与历史结果 | [unified-suite](experiments/unified-suite/README.md) |
| Overlay 读取、缓存与并发 | experiments/overlay/ |
| Aliyun 历史运行 | experiments/aliyun-main/、experiments/aliyun-traces/ |
| 早期输入采集和图表 | experiments/agent-fuse-observations/、experiments/agent-toolchain-observations/、experiments/workload-figure/ |
| 早期规划与环境 | experiments/early-plans/、experiments/cloud-setup/、experiments/replay-campaign/ |
| 共享数据与对象布局 | experiments/shared-data-campaign/、experiments/rustfs-range/ |

历史记录中的机器、路径、源码版本和结果属于其运行当时。需要执行历史代码时，从对应 Git 提交取出所需环境。当前准备和执行入口为 [SETUP](../docs/SETUP.md) 与 [HANDOFF](../docs/HANDOFF-C1-C3.md)。

早期移除的文件目录记录在 [history.json](history.json) 和既有 manifest 文件中，内容可从其记录的 Git 提交读取。
