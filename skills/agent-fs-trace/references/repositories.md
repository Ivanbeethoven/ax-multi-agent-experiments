# 仓库、版本和输入

## 实验与工具

| 地址 | 用途/固定版本 |
|---|---|
| https://github.com/Ivanbeethoven/ax-multi-agent-experiments | 公开实验仓库；本包来源基准提交dc48db5b4978aeffac7b33eb0386b27e95fe8f4e。部分采集改动未进入该基准提交，以本包source-manifest.json的逐文件摘要为准 |
| https://github.com/Ivanbeethoven/fuse-workload-replay | 私有早期Rust工具；v1输入与27操作样例，仅作历史来源 |
| https://github.com/anomalyco/opencode | OpenCode上游；本次固定npm包opencode-ai@1.19.4 |
| https://github.com/SWE-bench/SWE-bench | 评分代码；commit 02e7a74ffd0b707aab73d203fe87bdc7c76afc8e，本机包版本5.0.2 |
| https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified | 固定数据版本78f471bf655a3137b2e8a75af1501690ec009ec3，文件data/test-00000-of-00001.parquet |
| https://github.com/fusepy/fusepy | Python记录器的FUSE绑定，fusepy 3.0.1 |
| https://github.com/libfuse/libfuse | Linux FUSE用户态库；本机libfuse2为2.9.9，卸载使用fusermount3 |

数据集文件SHA256：`030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25`。入口校验文件原字节，不接受另一个版本重新导出后冒充相同输入。

Python环境实测：3.12、fusepy 3.0.1、Docker SDK 7.2.0、pyarrow 26.0.0。Rust/Cargo实测1.98.1，按包内Cargo.lock构建；不要改lock来迁就旧Cargo。

## 四个真实任务

| 题目 | 目标代码仓库 | Docker镜像标签 |
|---|---|---|
| django__django-10554 | https://github.com/django/django | swebench/sweb.eval.x86_64.django_1776_django-10554:latest |
| pytest-dev__pytest-10356 | https://github.com/pytest-dev/pytest | swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-10356:latest |
| scikit-learn__scikit-learn-25102 | https://github.com/scikit-learn/scikit-learn | swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25102:latest |
| sympy__sympy-13878 | https://github.com/sympy/sympy | swebench/sweb.eval.x86_64.sympy_1776_sympy-13878:latest |

入口从固定parquet选择这四题，保存base_commit和实际image_id。标签用于查找，镜像内容ID才是本次执行身份。源码由镜像的/testbed复制，题目由problem_statement提供；参考答案和测试补丁保留在宿主评分侧。

## 已有负载

GitHub较小分卷：实验仓库的`results/opencode-full-fuse-pytest-10356-20261023/`，bundle.json＋7个20 MiB以内分卷，原压缩总量127,774,151字节。

OSS完整归档：私有桶`brewfs-agent-traces-20261023-4ccdae73`，cn-hangzhou，前缀`full-fuse/20261023/`。四个文件为django.tar.gz、sklearn.tar.gz、sympy.tar.gz、pytest-full-02.tar.gz；URI、大小和完整SHA256已带在`assets/runtime/scenarios/k8s-containers/archives.json`。

OSS下载需要该桶的已授权身份。本包不含GitHub凭据、模型密钥、OSS凭据或原始大负载。

2026-10-26新增3份：deepseek-opencode-20261026/pytest.tar.gz、rust.tar.gz、io-compatible.tar.gz，以及该批metadata.tar.gz。[最新7份清单与下载命令](https://github.com/Ivanbeethoven/ax-multi-agent-experiments/blob/main/scenarios/k8s-containers/data-catalog.md)。本skill内assets/runtime保留原工具快照，新增数据使用仓库根scenarios/k8s-containers的最新清单与下载器。
