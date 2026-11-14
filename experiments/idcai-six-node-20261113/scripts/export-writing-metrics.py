#!/usr/bin/env python3
"""Export measured values with JSON pointers; never fill missing measurements."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def at(document, pointer):
    value = document
    for token in pointer.strip('/').split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def export(root, out):
    documents, metrics, cases = {}, [], []

    def load(relative):
        if relative not in documents:
            documents[relative] = json.loads((root / relative).read_text(encoding='utf-8-sig'))
        return documents[relative]

    def add_case(group, name, system, run, relative, pointer):
        obj = at(load(relative), pointer)
        assert obj['passed'] is True, (relative, pointer)
        row = dict(group=group, case=name, system=system, run=run,
                   passed=True, formal_sample=False, source_json=relative,
                   source_pointer=pointer, source_sha256=digest(root / relative))
        cases.append(row)
        return row

    def metric(case, phase, name, unit, suffix, scope):
        pointer = case['source_pointer'] + suffix
        value = at(load(case['source_json']), pointer)
        if value is None or value == 'absent':
            return
        if isinstance(value, str) and value.isdecimal():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError((pointer, value))
        metrics.append({**case, 'phase': phase, 'metric': name, 'value': value,
                        'unit': unit, 'source_pointer': pointer, 'scope': scope})

    old = 'evidence/ax-four-aa0966f7c0c2/c1-comparison.json'
    fixed = 'evidence/writing-sources-20261114/ax-four-5c6abaf21766/c1-comparison.json'
    selected = load('evidence/writing-sources-20261114/ax-four-5c6abaf21766/c1-progress.json')
    for s in selected['cases']:
        relative = old if s['run'] == 'ax-four-aa0966f7c0c2' else fixed
        doc = load(relative)
        matches = [(i, c) for i, c in enumerate(doc['cases'])
                   if (c['mode'], c['product']) == (s['mode'], s['product'])]
        assert len(matches) == 1 and doc['run'] == s['run']
        i, obj = matches[0]
        case = add_case('C1-IO', 'c1-' + s['mode'] + '-' + s['product'],
                        s['product'], s['run'], relative, f'/cases/{i}')
        metric(case, 'measure', 'measure_wrapper_seconds', 's', '/measure_wrapper_seconds',
               '测量命令外层耗时；不含后续排空与冷验证；不是 fio runtime')
        metric(case, 'measure', 'fio_job_runtime', 'ms', '/fio_job_runtime_ms', 'fio job runtime')
        for direction in ('read', 'write', 'sync'):
            fields = [('io_bytes', 'B'), ('total_ios', 'operations'), ('iops', 'operations/s'),
                      ('bw_bytes', 'B/s'), ('runtime', 'ms'), ('latency_mean_ns', 'ns')]
            for key, unit in fields:
                metric(case, direction, key, unit, f'/fio/{direction}/{key}',
                       'fio 应用侧统计；sync 单列；不是后端物理读写量')
            for percentile in ('50.000000', '95.000000', '99.000000'):
                if percentile in obj['fio'][direction]['latency_percentiles_ns']:
                    metric(case, direction, 'latency_p' + percentile.split('.')[0], 'ns',
                           f'/fio/{direction}/latency_percentiles_ns/{percentile}',
                           'fio 应用侧延迟；不可跨用例合并分位数')

    relative = 'evidence/writing-sources-20261114/c1-layout-summary.json'
    for i, obj in enumerate(load(relative)['profiles']):
        case = add_case('C1-Bq', 'c1-layout-' + obj['profile'], 'rustfs', obj['run'],
                        relative, f'/profiles/{i}')
        for key in ('full_reads', 'range_reads', 'shards'):
            metric(case, 'layout', key, 'count', '/' + key, '布局与读回正确性检查次数')
        for j, item in enumerate(obj['objects']):
            for key, unit in [('requested_block_bytes', 'B'), ('persisted_block_bytes', 'B'),
                              ('persisted_quantum_bytes', 'B'), ('returned_bytes', 'B'),
                              ('request_seconds', 's')]:
                metric(case, item['object'], key, unit, f'/objects/{j}/{key}',
                       '单次布局检查；缺失 q 或 hint 不填零；不证明性能优势')

    relative = 'evidence/ax-four-9bf61ed86533/c2-comparison.json'
    runs = {'brewfs-flat': 'ax-four-9bf61ed86533',
            'brewfs-workspace': 'ax-four-72e316106d56', 'juicefs': 'ax-four-65e01ef71d7e'}
    for i, obj in enumerate(load(relative)['arms']):
        arm = obj['arm']
        case = add_case('C2', 'c2-' + arm, arm, runs[arm], relative, f'/arms/{i}')
        for key, unit, scope in [
            ('replay_seconds', 's', 'Rust 活跃回放；不含准备、终态验证、冷重挂；不是 Agent 总时间'),
            ('operations', 'operations', '完整 trace 的回放操作数'),
            ('actual_read_bytes', 'B', '回放实际成功读取字节；不是 S3/物理盘流量'),
            ('actual_write_bytes', 'B', '回放实际成功写入字节；不是 S3/物理盘流量'),
            ('captured_repeated_read_ratio', 'ratio', '捕获负载属性；不是缓存命中率'),
        ]:
            metric(case, 'replay', key, unit, '/' + key, scope)

    relative = 'evidence/c1-c4-completed-20261114/receipts/ax-four-0ee20917f05d/c3-comparison.json'
    doc = load(relative)
    for i, obj in enumerate(doc['cases']):
        case = add_case('C3', 'c3-' + obj['arm'], obj['arm'], doc['run'], relative, f'/cases/{i}')
        for phase in ('verify-base', 'run', 'verify-touched'):
            metric(case, phase, 'wall_seconds', 's', f'/{phase}/wall_seconds',
                   'run 总时长包含下列操作子阶段，不能相加；run 前已全树预热；verify-touched 是另计的冷重挂验证')
        for key, unit in [('actual_read_bytes', 'B'), ('actual_write_bytes', 'B'),
                          ('verified_reads', 'operations'), ('writes', 'operations')]:
            metric(case, 'run', key, unit, '/run/' + key, '应用侧成功读写；不是后端流量')
        for j, phase in enumerate(obj['run']['phases']):
            for key, unit in [('wall_seconds', 's'), ('read_bytes', 'B'), ('write_bytes', 'B'),
                              ('reads', 'operations'), ('writes', 'operations'),
                              ('io_p50_ns', 'ns'), ('io_p95_ns', 'ns'), ('io_p99_ns', 'ns'),
                              ('application_p99_ns', 'ns')]:
                metric(case, phase['phase'], key, unit, f'/run/phases/{j}/{key}',
                       '保留原阶段名；前置全树校验已预热，cold-read 不代表严格冷缓存')

    relative = 'evidence/c1-c4-completed-20261114/receipts/c4-comparison.json'
    selected_stages = {'workload:prepare', 'workload:verify-base', 'workload:run',
                       'workload:verify-touched', 'import-workspace', 'clone-check',
                       'parent-before-check', 'child-write-check', 'parent-after-check',
                       'child-reopen-check'}
    for i, obj in enumerate(load(relative)['source']['cases']):
        system = 'juicefs' if 'juicefs' in obj['case'] else 'brewfs-workspace'
        case = add_case('C4', obj['case'], system, obj['run'], relative, f'/source/cases/{i}')
        for j, stage in enumerate(obj['stages']):
            if stage['name'] in selected_stages or stage.get('command_action') in ('fork', 'clone'):
                name = stage.get('command_action') or stage['name']
                metric(case, name, 'elapsed_seconds', 's', f'/stages/{j}/elapsed_seconds',
                       '阶段含义见交接；fork 不含发布准备；clone-check 含 clone；嵌套时间不相加；stay/move 不相减')
        if 'move' in obj:
            for key in ('mount_seconds', 'tree_check_seconds', 'elapsed_seconds', 'controller_elapsed_seconds'):
                metric(case, 'move-target', key, 's', '/move/' + key,
                       'elapsed 包含 mount/tree/写探针，controller 又包含 elapsed 与 SSH；不可相加；无工作区复制或 Agent resume')
        if 'publication_seconds' in obj:
            for key in ('publication_seconds', 'initialization_seconds'):
                metric(case, 'preparation', key, 's', '/' + key, '诊断 v7；独立准备阶段，不计入原生 fork')
            for key in ('registry_preparation_seconds', 'headless_publication_seconds', 'preparation_seconds'):
                metric(case, 'publication', key, 's', '/publication/' + key,
                       '原回执嵌套计时，不能与完整 publication_seconds 相加')

    assert Counter(c['group'] for c in cases) == {'C1-IO': 9, 'C1-Bq': 3, 'C2': 3, 'C3': 2, 'C4': 6}
    assert len({c['case'] for c in cases}) == 23
    identities = [(r['case'], r['phase'], r['metric']) for r in metrics]
    assert len(set(identities)) == len(identities), 'ambiguous metric row'
    # Re-resolve every cell against the source, independent of the extraction loops.
    for row in metrics:
        value = at(load(row['source_json']), row['source_pointer'])
        assert float(value) == float(row['value'])
        assert digest(root / row['source_json']) == row['source_sha256']
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in [('cases.csv', cases), ('metrics.csv', metrics)]:
        with (out / name).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    proof = {'schema': 'ax-writing-metrics-v1', 'passed': True, 'formal_sample': False,
             'case_count': len(cases), 'metric_count': len(metrics),
             'groups': dict(Counter(c['group'] for c in cases)),
             'sources': {p: digest(root / p) for p in sorted(documents)},
             'outputs': {n: digest(out / n) for n in ('cases.csv', 'metrics.csv')},
             'script_sha256': digest(Path(__file__))}
    (out / 'verification.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: proof[k] for k in ('passed', 'case_count', 'metric_count', 'groups')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    export(args.root.resolve(), args.out or args.root / 'evidence/writing-metrics-20261114')
