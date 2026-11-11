# 三个运行开关

当前定义统一在[当前方案：三个开关与消融](current/README.zh.md#三个开关与消融)，实际客户端、Packed 内存和服务端配置见[详细配置](current/CONFIGURATION.zh.md)。

计划接口：`--overlay on|off`、`--packed-metadata on|off`、`--dynamic-block on|off`。统一适配器尚未实现，当前 BrewFS CLI 不能直接使用这三个参数。

完整版为 workspace-v1 + Packed-v3；基础版 flat-v1 的动态块仍开启。关闭 Overlay 必须同引擎物化；关闭 Pack 必须保持 payload 布局；关闭动态块必须服务四开关关闭、全部上传者无 hint，并写入全新对象。三项都通过实际回执验收，不用切换编译 feature 做消融。
