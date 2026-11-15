"""Render every pair and stage from the audited summary JSON files."""
import argparse
import json
from pathlib import Path
import statistics


def render(directory):
    files = {key: json.loads((directory / (key + '-summary.json')).read_text())
             for key in ('small', 'large16', 'large64')}
    assert all(value['passed'] for value in files.values())
    small = {g['scenario']: g for g in files['small']['groups']}
    groups = [('2 文件 / 64 MiB / 257 轮', small['two-files']),
              ('32 文件 / 64 MiB / 257 轮', small['small-files-32']),
              ('10,000 文件 / 16 MiB / 3 轮', files['large16']['groups'][0]),
              ('10,000 文件 / 64 MiB / 3 轮', files['large64']['groups'][0])]
    lines = ['# A355 热读逐次结果', '',
             '由 `report.py evidence` 生成。耗时单位为 ms，包含该批全部计时轮次；预热不计入。',
             '百分比 = 100 ×（候选耗时 / 对照耗时 − 1），正数表示变慢。总表使用中位耗时相除。', '',
             '这些是读取测量结果；混合读写验收未通过，具体失败见 README。', '',
             '| 输入 / 上层缓存 / 轮数 | 原版 Overlay | 候选 Overlay | 直接 lower | 候选/原版 − 1 | 候选/lower − 1 |',
             '|---|---:|---:|---:|---:|---:|']
    for label, g in groups:
        m = g['median_elapsed_ms']
        lines.append(f"| {label} | {m['production']:.6f} | {m['candidate']:.6f} | {m['packed-direct']:.6f} | {g['candidate_vs_baseline_pct']:+.6f}% | {g['candidate_vs_lower_pct']:+.6f}% |")
    for label, g in groups:
        lines += ['', '## ' + label, '',
                  '| 配对 | 原版 Overlay | 候选 Overlay | 直接 lower | 候选/原版 − 1 | 候选/lower − 1 |',
                  '|---|---:|---:|---:|---:|---:|']
        for p in g['pairs']:
            t = p['elapsed_ms']
            lines.append(f"| {p['pair']} | {t['production']:.6f} | {t['candidate']:.6f} | {t['packed-direct']:.6f} | {p['candidate_vs_baseline_pct']:+.6f}% | {p['candidate_vs_lower_pct']:+.6f}% |")
        lines += ['', '各阶段为跨重复的算术平均值，不能与上面的中位总耗时直接相加。', '',
                  '| 阶段 | 原版 | 候选 | 候选 − 原版 | 候选/原版 − 1 |',
                  '|---|---:|---:|---:|---:|']
        a, b = (g['mean_stages_ms'][k] for k in ('production', 'candidate'))
        stages = [('打开目录', ('directory_open',)), ('列目录', ('directory_list',)),
                  ('open 文件', ('file_open',)), ('fstat 文件', ('file_fstat',)),
                  ('pread 文件', ('file_pread',)), ('close 文件', ('file_close',)),
                  ('其余调用与计时循环', ('directory_relative_stat', 'directory_close', 'caller_other', 'between_rounds'))]
        for name, keys in stages:
            before, after = (sum(v[k] for k in keys) for v in (a, b))
            lines.append(f'| {name} | {before:.6f} | {after:.6f} | {after-before:+.6f} | {100*(after/before-1):+.6f}% |')
        lines += ['', '下面的计数取各单元中位数；Redis 审计在计时结束后另做一次遍历，包含心跳等后台活动。', '',
                  '| 路径 | 行缓存命中/未命中 | 区间查询/空区间命中 | 目录查询/空目录命中 | 计时 Redis 执行命令 | 独立审计客户端/Lua 命令 |',
                  '|---|---:|---:|---:|---:|---:|']
        for arm, name in [('production', '原版'), ('candidate', '候选'), ('packed-direct', '直接 lower')]:
            ts = [t for t in g['trials'] if t['arm'] == arm]
            def count(suffix):
                return statistics.median(t['workspace_counters'].get('brewfs_workspace_' + suffix + '_total', 0) for t in ts)
            server = statistics.median(t['redis_executed_commands'] for t in ts)
            clients = statistics.median(t['untimed_single_pass_wire_audit']['client_commands'] for t in ts)
            lua = statistics.median(t['untimed_single_pass_wire_audit']['lua_commands'] for t in ts)
            lines.append(f"| {name} | {count('row_cache_hits'):g} / {count('row_cache_misses'):g} | {count('upper_extent_queries'):g} / {count('upper_extent_empty_cache_hits'):g} | {count('upper_directory_queries'):g} / {count('upper_directory_empty_cache_hits'):g} | {server:g} | {clients:g} / {lua:g} |")
            assert all(not any(t[p].values()) for t in ts for p in ('first_objects', 'steady_objects'))
    lines += ['', '## 64 MiB 组的缓存刷新位置', '',
              '所有六个 Overlay 单元总计都有 10,000 次区间查询。第三次候选只是刷新位置不同；不能把该次较快归结为区间查询总数减少。', '',
              '| 配对/路径 | 预热 ms | 三轮耗时 ms | 首轮/后两轮区间查询 |',
              '|---|---:|---|---:|']
    for t in groups[-1][1]['trials']:
        if t['arm'] == 'packed-direct':
            continue
        a = t['first_workspace_counters']['brewfs_workspace_upper_extent_queries_total']
        b = t['steady_workspace_counters']['brewfs_workspace_upper_extent_queries_total']
        lines.append(f"| {t['pair']}/{t['arm']} | {t['warmup_ms']:.6f} | {' / '.join(f'{x:.6f}' for x in t['round_elapsed_ms'])} | {a:g} / {b:g} |")
    lines += ['', '54 个读取单元的首轮及后续计时阶段均为 0 对象请求、0 对象字节。',
              '完整逐文件 open/fstat/pread/close、逐轮目录操作、原始计数与命令保存在压缩归档中。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(render(parser.parse_args().directory), end='')
