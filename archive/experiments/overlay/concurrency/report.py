#!/usr/bin/env python3
"""Render paired medians, each repetition, phase costs and real backend counts."""
import argparse
import json
from pathlib import Path
import statistics


def render(root):
    documents = [json.loads((root / (g + '-summary.json')).read_text()) for g in ('small', 'large64')]
    assert all(d['passed'] for d in documents)
    groups = [(d, g) for d in documents for g in d['groups']]
    lines = ['# A355 修复后热读对比', '',
        '旧版与候选采用相同输入；直接 lower 是同引擎只读路径。单位 ms，计时不含预热。',
        f"旧版二进制 SHA256：`{documents[0]['baseline_binary_sha256']}`。",
        f"候选二进制 SHA256：`{documents[0]['candidate_binary_sha256']}`。",
        '直接 lower 使用' + ('候选' if documents[0]['settings'].get('lower_build', 'baseline') == 'candidate' else '旧版') + '二进制；各单元另核验实际 SHA256。',
        '开销 = 100 ×（候选耗时 / 对照耗时 − 1）。汇总用展示的中位耗时相除；正数表示变慢。', '',
        '| 输入 | 旧 Overlay | 新 Overlay | 直接 lower | 新/旧 − 1 | 新/lower − 1 |',
        '|---|---:|---:|---:|---:|---:|']
    for d, g in groups:
        m = g['median_elapsed_ms']
        lines.append(f"| {g['scenario']} | {m['production']:.6f} | {m['candidate']:.6f} | {m['packed-direct']:.6f} | {g['candidate_vs_baseline_pct']:+.6f}% | {g['candidate_vs_lower_pct']:+.6f}% |")
    for d, g in groups:
        lines += ['', '## ' + g['scenario'], '',
                  f"行缓存 {d['settings']['upper_row_cache_bytes']} B，payload 缓存 {d['settings']['retained_payload_bytes']} B。", '',
                  '| 配对 | 旧 Overlay | 新 Overlay | 直接 lower | 新/旧 − 1 | 新/lower − 1 |',
                  '|---|---:|---:|---:|---:|---:|']
        assert len(g['pairs']) >= 3
        for p in g['pairs']:
            t = p['elapsed_ms']
            lines.append(f"| {p['pair']} | {t['production']:.6f} | {t['candidate']:.6f} | {t['packed-direct']:.6f} | {p['candidate_vs_baseline_pct']:+.6f}% | {p['candidate_vs_lower_pct']:+.6f}% |")
        lines += ['', '阶段数为各次重复的算术平均值，不能与上表中位总耗时直接相加。', '',
                  '| 阶段 | 旧 Overlay | 新 Overlay | 差值 ms | 变化 |',
                  '|---|---:|---:|---:|---:|']
        for stage in g['mean_stages_ms']['production']:
            a, b = (g['mean_stages_ms'][arm][stage] for arm in ('production', 'candidate'))
            lines.append(f'| {stage} | {a:.6f} | {b:.6f} | {b-a:+.6f} | {100*(b/a-1):+.6f}% |')
        lines += ['', '计数为重复之间的中位数；Redis commandstats 包括 Lua 内部命令，不等于网络往返数。', '',
                  '| 路径 | 行缓存 hit/miss | 区间查询/空命中 | 目录查询/空命中 | Redis 命令 | 独立审计客户端/Lua 命令 |',
                  '|---|---:|---:|---:|---:|---:|']
        for arm in ('production', 'candidate', 'packed-direct'):
            trials = [t for t in g['trials'] if t['arm'] == arm]
            assert all(not any(t[p].values()) for t in trials for p in ('first_objects', 'steady_objects'))
            def count(suffix):
                return statistics.median(t['workspace_counters'].get('brewfs_workspace_' + suffix + '_total', 0) for t in trials)
            server = statistics.median(t['redis_executed_commands'] for t in trials)
            clients = statistics.median(t['untimed_single_pass_wire_audit']['client_commands'] for t in trials)
            lua = statistics.median(t['untimed_single_pass_wire_audit']['lua_commands'] for t in trials)
            lines.append(f"| {arm} | {count('row_cache_hits'):g}/{count('row_cache_misses'):g} | {count('upper_extent_queries'):g}/{count('upper_extent_empty_cache_hits'):g} | {count('upper_directory_queries'):g}/{count('upper_directory_empty_cache_hits'):g} | {server:g} | {clients:g}/{lua:g} |")
        lines += ['', '计时阶段对象读取请求与字节均为 0。Redis MONITOR 审计在计时结束后单独执行。']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(render(parser.parse_args().directory), end='')
