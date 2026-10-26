# 2026-10-24 验证记录

2026-10-26公开后：[28个对象匿名读取与列表验证](oss-public-access-check.json)通过；默认HTTPS下载器实际下载元数据并核对SHA256通过，[7项下载测试](public-download-tests.txt)通过。下方403记录属于公开前状态。

2026-10-26补充：[7份OSS对象核查](oss-catalog-check.json)验证远端大小/SHA256元信息；公网带身份访问成功，匿名HEAD返回403。新增下载/校验脚本的[5项测试](archive-catalog-tests.txt)通过。本次未重放新增的大归档。

状态：唯一外部对照为JuiceFS。部署、采集与传输脚本已准备；正式规模计划扩至128个客户端、512个并发工作区。真实平台和多容器协调尚未接通。没有申请云资源、调用模型或上传新数据。

## 脚本验证

`python3 -m unittest discover -s scenarios/k8s-containers/tests -v` 覆盖：

- 自己的cgroup路径解析、namespace根和挂载路径转义；不退回宿主机根。
- 缺指标与计数器重置保留空值。
- 计划数量和固定输入相同，每个主组worker都分到每种trace各4份。
- 正常退出、非零退出、超时、直接父进程退出后仍能清理忽略TERM的子孙进程。
- 截断日志、采样序号断裂不能标为完整。
- 禁止在被测目录内写采样结果；初始树不覆盖，终检发现内容变化。

本轮还增加：HTTP指标端点和原始样本、缺指标不填零、静态监控配置、带鉴权的实际结果传输、增长文件重试和空文件保留、坏摘要/冲突/路径越界拒绝、全任务共享超时、4—128客户端任务分配。

最新结果见[WSL测试输出](unittest-wsl.txt)与[受限容器测试输出](unittest-restricted-container.txt)。受限容器为UID/GID 65534、`--cap-drop ALL`、`--security-opt no-new-privileges`、只读rootfs、无外部网络、1 CPU、256 MiB内存、64进程限制，只有64 MiB临时目录。没有 `/dev/fuse` 或宿主机cgroup挂载。容器退出后自动删除。

Prometheus 3.15.1的promtool和OTel Collector contrib 0.163.2实际解析生成的配置，结果见[原生配置检查](monitor-config-check.json)。下载的官方包SHA256分别为`6a726885328ad9efebbf36e876ce2fb6225468a71fd1e7ce3ba666d5f4d97a84`和`cc19dd6a1b85ca694ef16655688e597f693d0e24d2baecdd50e2db597819eeff`。这是配置检查，不是RustFS/Redis/JuiceFS已在目标平台运行的证明。

起步计划为6批、24次回放，见[计划样例](smoke-plan.example.json)；正式规模组为12批、2,544次/系统，见[规模计数](scale-plan-counts.json)。计划生成不启动任务。

测试镜像ID：`sha256:d59c3d30abb895e37ac0ae225bf1a5deb6b55400086480d1a2bcafe5cb1d630f`，本地已有SWE-bench pytest镜像。镜像tag不作为可复现身份。

## 完整已有轨迹

用Cargo 1.98.1按原Cargo.lock编译release，二进制SHA256：`f3e30bc96510ce878af594c048394989589fb67d3f0313b6f37073b2628c0517`。

WSL使用原 `pytest-full-02` 案例运行 `run_case.py`，初始树复制/核验、Rust回放、最终树核验三个阶段退出均为0。

| 检查项 | 结果 |
|---|---:|
| 回调数 | 1,747,109 |
| 实际读字节 | 281,774,624 |
| 实际写字节 | 194,706,661 |
| 预期错误次数 | 45,525 |
| 剩余句柄 | 0 |
| 最终树 | 一致 |
| Rust严格回放时间 | 20.32723394秒 |
| 回放阶段资源样本 | 23条，序号连续，无损坏行 |
| 最大采样间隔 | 1.013009069秒 |

此次是脚本功能检查，不是监控开销测量，也不是Kubernetes或存储系统性能对比。不能拿这个20.3秒和之前其他环境的7.8秒算退化。WSL的 `/init.scope` 含其他进程，组级内存约22 GB不能解释为回放器用量；数据明确标 `own_cgroup_container_boundary_unconfirmed`。

17个原始小文件已复制到 [local-pytest](local-pytest/)，包含每阶段environment、samples、observer-result、stdout/stderr和最终结果。大输入和工作区保留在原本地位置，未复制进仓库。原始命令未记录参数明文；运行身份、计数和结果可追查。

## 专项审查后修复

根据researchwrite方法进行一次独立容器计量审查。三个实质问题已修复并增加相应检查：负载种类与worker固定绑定、超时后子孙进程可能残留、汇总未校验采样序号。原计划的冷缓存、物理I/O和真实Agent提速结论均未带入本方案。

下一步需要目标环境的probe、持久目录与可达地址，再实际验收JuiceFS挂载、Redis持久化和RustFS指标。多容器协调器、容量测试执行器和迁移执行器仍待接入；不能将任务计划当成已经完成的规模测试。
