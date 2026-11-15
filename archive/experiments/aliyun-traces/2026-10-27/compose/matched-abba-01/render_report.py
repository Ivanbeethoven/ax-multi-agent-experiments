"""Render the already-verified local analysis; no workload or remote actions."""
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).parent
s = json.loads((ROOT / 'summary.json').read_text())
rows = s['sessions']
labels = list(rows[0]['cases'])

def span(values, digits=3):
    return f'{min(values):.{digits}f}–{max(values):.{digits}f}'

def values(version, label, key):
    return [row['cases'][label][key] for row in rows if row['version'] == version]

lines = [
    '# 固定工作量 B–C–C–B：收尾成本诊断', '',
    '本组在同一台 500 GiB PL1 云节点完成 4 个新后端会话。B 为 baseline，C 为冻结 C4。所有 16 个作业的精确字节、seed 354、命令退出码、drain 和自然卸载均通过独立复核。目标 direct randwrite 本次没有复现 C4 额外数秒收尾；顺序写的 C4 close 成本则确实更高，不能遗漏。此结论只覆盖本组固定工作量诊断，不等同于普遍性能验收。', '',
    '## 证据与配置', '',
    f'- 归档 SHA256：`{s["archive_sha256"]}`；重新核验 {s["manifest_files_rehashed"]} 个成员的大小和 SHA256。',
    f'- Baseline：`{s["baseline_sha256"]}`。',
    f'- C4：`{s["candidate_sha256"]}`；源码包：`{s["source_archive_sha256"]}`。',
    '- 每会话顺序均为 seqwrite direct=0、seqwrite direct=1、randwrite direct=0、randwrite direct=1。分别写入 5,326,766,080 / 6,337,593,344 / 5,062,524,928 / 3,028,287,488 B，合计 19,755,171,840 B。',
    '- 所有作业：512 MiB 工作集、4 MiB 块、QD1、numjobs=1、io_uring、seed 354，移除 time_based/runtime，没有整体截止。4 会话使用相同冻结镜像，分别使用新后端和缓存目录。',
    '- 每会话 4 项 post-write drain 均为 0 s，pending/dirty/buffer_dirty 均为 0；每会话真实 mount child 自然退出 0，正常卸载返回 0，无剩余本项目容器。', '',
    '## 每个作业的实际计时', '',
    'Active 是 fio write.runtime；墙钟是 before_spawn→after_wait 的 monotonic_ns，包含受限 strace。吞吐为精确字节/该墙钟。最后 close 是目标数据文件最后一次 close 的 strace -T 自身持续时间。总 lat 是 fio write.lat_ns.mean，不是 clat。', '',
    '| 会话 | 作业 | Active s | 命令墙钟 s | 墙钟 MiB/s | 最后 close s | 全部 close s | 总 lat mean ms |',
    '|---|---|---:|---:|---:|---:|---:|---:|',
]
for row in rows:
    for label, case in row['cases'].items():
        vals = [case[k] for k in ('fio_active_write_seconds', 'command_wall_monotonic_seconds', 'case_wall_mib_s', 'final_close_seconds', 'all_target_close_seconds', 'write_total_lat_mean_ms')]
        lines.append(f'| {row["index"]} {"B" if row["version"] == "baseline" else "C"} | {label.removeprefix("fio-")} | ' + ' | '.join(f'{v:.6f}' for v in vals) + ' |')
lines += ['', '## 两次 B 与两次 C 的描述性范围', '',
          '每格都是本组两次会话的 min–max；百分比是两个命令墙钟均值的 C/B−1，不是置信区间或显著性检验。', '',
          '| 作业 | B 墙钟 s | C 墙钟 s | 墙钟均值 C/B−1 | B 最后 close s | C 最后 close s | B 总 lat mean ms | C 总 lat mean ms |',
          '|---|---:|---:|---:|---:|---:|---:|---:|']
for label in labels:
    b, c = values('baseline', label, 'command_wall_monotonic_seconds'), values('candidate', label, 'command_wall_monotonic_seconds')
    lines.append(f'| {label.removeprefix("fio-")} | {span(b)} | {span(c)} | {(mean(c)/mean(b)-1)*100:+.3f}% | ' +
                 ' | '.join(span(values(v, label, k)) for k in ('final_close_seconds', 'write_total_lat_mean_ms') for v in ('baseline', 'candidate')) + ' |')
lines += ['', 'Direct randwrite 的最后 close：B 为 0.710985–0.713546 s，C 为 0.699512–0.717253 s；最后 close 的范围重叠，命令墙钟均值仅相差约 0.075%。前三项前置写入完全相同，补上了早期定时负载写入量不同的混杂控制。', '',
          '但 seqwrite 的 close 分布发生变化：direct=0 全部 close 的和 B 为 ' + span(values('baseline', labels[0], 'all_target_close_seconds')) + ' s、C 为 ' + span(values('candidate', labels[0], 'all_target_close_seconds')) +
          ' s；direct=1 为 B ' + span(values('baseline', labels[1], 'all_target_close_seconds')) + ' s、C ' + span(values('candidate', labels[1], 'all_target_close_seconds')) + ' s。C 的每次 write 总 lat mean 同时更低，整体命令时间接近；这说明仅看 write latency 会遗漏 close 中的成本，不能据此断言成本原因或简单将二者相加。', '',
          '固定 io_size 超过 512 MiB，四项各有 11 / 13 / 11 / 7 次目标文件 close（含准备阶段）；其中有多次文件周转，不能只取准备和末尾两条，也不能把全部 close 的和当额外收尾。全部原始 close 引用、持续时间和顺序保存在 summary.json。', '',
          '## 最后 close 与最长 close 的口径核对', '',
          'Direct randwrite 的四轮目标都是 fd 8、路径 `/mnt/brewfs/.perf-fio-randwrite-direct1/brewfs-randwrite.0.0`。最后指该文件 close 的时间顺序末项；最长指全部周转 close 中的最大 -T。两者不是同一事件。这里只比较 strace 自己的时间戳和 duration，未与 monotonic 起点相减。', '',
          '| 会话 | 最后 close s | 最后事件文件:行 | 最长 close s | 最长事件文件:行 |',
          '|---|---:|---|---:|---|']
for row in rows:
    case = row['cases']['fio-randwrite-direct1']
    lines.append(f'| {row["index"]} {"B" if row["version"] == "baseline" else "C"} | {case["final_close_seconds"]:.6f} | {case["final_close_evidence"].split("/")[-1]} | {case["maximum_close_seconds"]:.6f} | {case["maximum_close_evidence"].split("/")[-1]} |')
lines += ['', '## clat 分位：单独列出', '',
          '以下 p50/p99 仅是 completion latency，单位 ms；不是总 lat 的分位。总 lat mean 已在上表。', '',
          '| 作业 | B clat p50 范围 ms | C clat p50 范围 ms | B clat p99 范围 ms | C clat p99 范围 ms |',
          '|---|---:|---:|---:|---:|']
for label in labels:
    cells = []
    for p in ('50.000000', '99.000000'):
        for version in ('baseline', 'candidate'):
            cells.append(span([r['cases'][label]['write_clat_percentiles_ms'][p] for r in rows if r['version'] == version], 6))
    lines.append('| ' + label.removeprefix('fio-') + ' | ' + ' | '.join(cells) + ' |')
lines += ['', '## 整个固定序列成本', '',
          '| 会话 | 4 项 active 和 s | 4 项命令墙钟和 s | 原 compose 命令 s | 会话端到端 s |',
          '|---|---:|---:|---:|---:|']
for row in rows:
    lines.append(f'| {row["index"]} {"B" if row["version"] == "baseline" else "C"} | ' + ' | '.join(f'{row[k]:.6f}' for k in ('sum_four_active_write_seconds', 'sum_four_command_wall_seconds', 'compose_command_wall_seconds', 'session_end_to_end_monotonic_seconds')) + ' |')
b = [r['sum_four_command_wall_seconds'] for r in rows if r['version'] == 'baseline']
c = [r['sum_four_command_wall_seconds'] for r in rows if r['version'] == 'candidate']
lines += ['', f'四项命令墙钟和均值 C/B−1 为 {(mean(c)/mean(b)-1)*100:+.3f}%。会话端到端包括新后端准备和清理；它与 4 项命令之和不是同一指标。整组从第一个会话启动到最后一个会话退出为 {s["whole_abba_host_monotonic_span_seconds"]:.6f} s，包含会话间端口等待等间隔。', '',
          '## 时钟和推断边界', '']
cases = [case for row in rows for case in row['cases'].values()]
lines += [
    '- shell tool_start→tool_end 减命令墙钟范围 ' + span([c['shell_outside_command_seconds'] for c in cases], 6) + ' s；它来自同一 monotonic 时钟，不能用整数秒 perf-summary 推算。',
    '- 本组 realtime elapsed−monotonic elapsed 范围 ' + span([c['realtime_minus_monotonic_elapsed_seconds'] for c in cases], 9) + ' s；monotonic_raw elapsed−monotonic elapsed 范围 ' + span([c['raw_minus_monotonic_elapsed_seconds'] for c in cases], 9) + ' s。三者独立记录，没有 epoch 与 monotonic 相减归因。',
    '- 受限 strace 没有记录 write/io_uring，无法确定最后 write 的精确边界。命令墙钟−fio active 也不是纯 flush。close 的 -T 包含 close 内部等待，不能区分 FUSE flush、release 和其他工作。',
    '- 每版本只有同一组 ABBA 中的两次顺序会话；不能把 I/O 次数当重复样本。主机、内核、Docker 版本与早期 WSL 不同，本组不用于跨机性能排名。',
    '- 旧宽谱 compose、全量 trace 与 CI 是另外的证据，本报告不替代它们，也不自动作验收决定。', '',
    '机器可读数据：[summary.json](summary.json)、[cases.csv](cases.csv)；只读解析器：[analyze_abba.py](analyze_abba.py)。原始证据未修改。', '']
(ROOT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
print('REPORT.md rendered from independently verified summary.json')
