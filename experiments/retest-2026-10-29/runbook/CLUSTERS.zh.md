# 四组负责人和执行交接

四组沿用[总览](../PLAN.zh.md)，以[新版部署手册](DEPLOYMENT.zh.md)的ECS/自备两版资源合同执行。一个owner监督本组全部工作Agent，owner不计入被测并发；当前phase=prepare，不部署或开跑。

| 唯一负责人 | 负责实验 | 池内安排 | 独立执行方prompt |
|---|---|---|---|
| c1-owner | Agent trace主对照、BrewFS内部对照、独立上传策略消融 | 同一共享服务池顺序测臂；同步主线和writeback分开 | [C1](agents/C1.prompt.md)；[pilot](agents/C1-pilot.prompt.md) |
| c2-owner | Pack/Native+TiKV海量只读ML | 同语料/读集；三个PD/TiKV节点属于本组 | [C2](agents/C2.prompt.md) |
| c3-owner | RustFS布局与并发S3 | 五臂完整区组；两请求节点的全局在途总和为N | [C3](agents/C3.prompt.md) |
| c4-owner | fork/clone/cp、跨节点stay/move | F/M顺序复用；源目标独立，服务池不跟Agent移动 | [C4](agents/C4.prompt.md) |

四组可并行准备；并行测量须使用各自独立可变服务池。共享的只有冻结源码、构建产物与只读输入；构建/分发/哈希扫描在计时外。组内baseline若要并行，再给完整独立等规格池并轮换放置，不隐瞒服务争用。

## 调度单

主会话把对应prompt与以下实际信息一起交接，路径均来自执行方，不猜旧Windows目录、SSH别名或云实例：

```json
{
  "campaign_id": "由执行方填写",
  "campaign_profile": "ecs-20261101",
  "cluster_id": "C1",
  "owner_agent": "c1-owner",
  "provider": "aliyun",
  "isolation": "docker",
  "profile": "pilot",
  "study": "baseline",
  "phase": "prepare",
  "execution_enabled": false,
  "run_dir": null,
  "frozen_repo_sha": null,
  "plan_path": null,
  "plan_sha256": null,
  "inventory_path": null,
  "input_verification": null,
  "source_and_artifact_pins": null,
  "budget_and_resource_authorization": null,
  "result_retention": null
}
```

provider=provided时不填ECS假ID，使用真实资产编号；C2/C3/C4没有C1 pilot，选择main作为待审计划，资格另记。study=writeback仅C1/C4、main，能力未就绪不得开跑。输入下载权限不授予采购、改盘或运行完整矩阵。

## 监督合同

1. 安全同步仓库，记录并冻结实际完整SHA；先读AGENTS、总览、部署、数据与自己prompt。RustFS固定指定分支，构建一次并核源码/镜像/二进制对应。
2. 生成本组计划和准确节点模板；填真实资源与预算、host-key、独占路径、数据盘和宿主关系。执行已有明确授权的范围，不因为其他会话完成了下载就扩大本组。
3. 一次完成一个操作：准备步骤spec→执行→保存退出码与输出SHA→检查→下一步。没有包办建机、全矩阵、重试、删机的总脚本。
4. 计时前核全部worker就绪，计时中不插入监督思考。所有原始结果/慢值/失败/缺测保留；新尝试新attempt，不盲重投、不按结果选重复。
5. 当前进度记录preparing/ready/running/blocked/collected/cleaned与实际证据。未完成适配如实列缺项，不能以空模板或true字段代替验收。
6. 完整归档下载核SHA，确认回放/构建/FUSE/服务停止后再按本组精确归属清理。自备保留服务器和原数据，云资源释放以查询确认为准。共用管理入口由主会话收尾。

C4 fork重试保持原key/请求/子项ID，不回退已推进head；删除前drain、卸载与lease检查，不能以DELETE代替迁移释放写者。Manager单实例、原journal恢复与namespace所有权规则见[MANAGER](MANAGER.zh.md)，不能跨组共管catalog。C1默认不部署仍用SQLite journal的Manager。

主会话负责统一版本、预算、跨组冲突和结果汇总；各owner负责本组逐步资格与证据。未授权运行时，交付具体配置、脚本适配和缺项即可，不启动任何实验。
