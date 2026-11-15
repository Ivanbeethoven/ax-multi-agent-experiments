# 本轮修改的依据

日期：2026-10-24。范围：在原实验旁增加容器方案，不改写原云主机方案，不申请云资源。

## 已知事实与待确认项

- 用户确认：只能进入已分配的容器，不能创建 Pod、Service，不能管理控制平面；可申请较多容器。
- 用户本轮收敛：唯一外部对照为JuiceFS CE＋Redis＋RustFS；此前云主机方案中的其他基线不进入本轮。
- 未确认：FUSE 设备和挂载权限、跨容器连通性、持久目录、实际节点位置、CPU/内存上限、镜像和二进制兼容性。
- 已有四份完整挂载层 trace：pytest、Django、scikit-learn、SymPy。它们是四个案例；复制 16 次不等于 64 个独立任务。原 L16 计划是另一组输入。
- 本地 Rust 回放支持串行重演 POSIX 操作、错误码与内容核验；没有模型等待回放、多容器协调、单个 syscall 延迟直方图。运行时间含 JSON、blob、SHA256、路径解析。Python 全树预检会预热缓存。
- 原方案依赖 ECS、独立云盘和部分集群部署权限；这些能力不自动带入当前环境。

## 证据与结论边界

| 判断 | 依据 | 允许的结论 |
|---|---|---|
| 回放普通已挂载路径不需要 AX 控制器 | `tools/full-fuse-replay/src/main.rs` 直接调用 libc | 可先做文件负载测试；不是 AX 部署结果 |
| 挂载新 FUSE 需要平台提供设备与许可 | [Linux FUSE](https://docs.kernel.org/filesystems/fuse/fuse.html)、[Pod 安全标准](https://kubernetes.io/docs/concepts/security/pod-security-standards/) | 容器内 root、存在 `/dev/fuse` 都不能单独证明可挂载；不修改平台策略 |
| 容器数不等于 Pod 数或节点数 | [Kubernetes Pod](https://kubernetes.io/docs/concepts/workloads/pods/) | 同一 Pod 的容器可能共享网络；节点位置需平台记录 |
| cgroup 计量有明确范围 | [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html) | 采集自己的 cgroup；可见的父组或宿主机计数不能冒充单容器计数 |
| `kubectl top` 不足以完成性能采集 | [资源指标管线](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-metrics-pipeline/) | 其主要是 CPU/内存；本方案不要求这个权限 |
| 临时卷不能作为迁移持久性证据 | [临时卷](https://kubernetes.io/docs/concepts/storage/ephemeral-volumes/) | 保留容器时可测性能；重建后恢复需实际持久后端 |

## 问题与实验的关系

RQ1 用固定资源测工作区数与排队；RQ2 测分叉、保存与共享；RQ3 用相同文件轨迹比较回放成本；RQ4 在已准备好的两个容器间检查状态交接。每个问题仍由单独设计开关解释。单任务 I/O 不占主要时间时，不转述为 Agent 加速。

## 本轮文档约定

README写实验清单，deployment写进程/端口/启动命令，workload写工具和OSS输入，observability写字段与采集链路。脚本只声称实际实现的能力。不能观测的后台物理磁盘、放置位置、跨节点故障保留为未知。
