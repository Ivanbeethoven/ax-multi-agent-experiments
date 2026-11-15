# 固定工作量收尾对照

先读 [REPORT.md](REPORT.md)。这是同机一组 B–C–C–B 诊断，不是普遍性能证明。

- [cases.csv](cases.csv)：16 个作业的时间、吞吐和延迟。
- [summary.json](summary.json)：逐事件 close、时钟域、卸载和 drain，保留原始文件与行号。
- `raw/`：未改写的必要原始 fio、clock、strace、latency、drain、exit 和环境身份资料；summary 中的证据路径以此目录为根。
- [publication-manifest.json](publication-manifest.json)：本包文件大小与 SHA256；[privacy-check.json](privacy-check.json)：发布前检查记录。

只用 Python 标准库复算（不接触云端，不运行负载）：

```sh
python analyze_abba.py --public-root .
```

解析器先验证发布清单，再核验原始证据并写入 `reproduced/summary.json` 与 `reproduced/cases.csv`。已发布的摘要记录原归档 548 文件复核；复算摘要的 `manifest_files_rehashed` 只计本包必要子集，其余 session 数值应完全一致。不要把最终 close 与耗时最长的中途 close 混淆；报告已并列两者。

原归档未公开上传凭据、私有配置或签名 URL；本包没有实验部署脚本，也不会更改现有实验。完整来源和取舍见 [provenance.json](provenance.json)。
