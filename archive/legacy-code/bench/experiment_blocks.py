"""Independent functional-check groups; performance pairing is defined in BLOCKS.zh.md."""
from bench.experiment_modes import MODES


def cases(experiment, modes=None):
    return ['-'.join(x for x in (experiment, mode, placement) if x)
            for mode in (modes or MODES[experiment])
            for placement in (('stay', 'move') if experiment == 'C4-handoff' else (None,))]


BLOCKS = {
    'c1-io': {'question': '基础读写的完成时间和正确性', 'cases': cases('C1-io')},
    'c1-layout': {'question': '动态块和读取粒度是否减少物理 I/O', 'cases': cases('C1-layout')},
    'c2-trace': {'question': '相同完整 trace 在三个系统上如何表现',
                 'cases': cases('C2-replay', ('brewfs-basic', 'brewfs-full', 'juicefs'))},
    'c2-overlay': {'question': 'lower 已热时 Overlay 增加多少运行开销', 'cases': cases('C2-runtime')},
    'c2-ablations': {'question': 'Pack、动态块和完整方案各有多少收益',
                     'cases': cases('C2-replay', ('brewfs-full', 'packed-off', 'dynamic-off', 'all-off'))},
    'c3-smallfiles': {'question': '共享小文件读取和首次修改的成本', 'cases': cases('C3-smallfiles')},
    'c4-fork': {'question': '原生 fork 与原生 clone 的派生成本', 'cases': cases('C4-fork')},
    'c4-handoff': {'question': '工作区换机后重开和继续文件读写的成本', 'cases': cases('C4-handoff')},
}


def selected_cases(blocks):
    """Keep each group's order and deduplicate only within this functional run."""
    result = []
    for name in blocks:
        for case in BLOCKS[name]['cases']:
            if case not in result:
                result.append(case)
    return result
