#!/usr/bin/env python3
"""Validate all three complete, released nodes before rendering a diagnostic.

--check is read-only and produces no plots or synthetic data. Node directories
are arguments; private paths and configurations are never copied to the report.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tarfile

CONFIGS = {'100g-pl0': (100, 'PL0'), '500g-pl0': (500, 'PL0'), '500g-pl1': (500, 'PL1')}
METHODS = ('native', 'native-overlay', 'native-overlay-pack', 'juicefs')
LABELS = ('native / C4', 'native-overlay / C4', 'native-overlay-pack / C4', 'JuiceFS')
SOURCE_SHA = 'd609a429dfce0943114717495a76c164598df36a4564768fc346156548ea2783'
BREWFS_SHA = 'c1f6de4e5fa06873cbd2e281c697a65470ad602b9c5fc4b20b43b9fe74e3c3d2'
TRACE_SHA = '9953d90517eaf520814150f80abccb11f9d74b7ac84ecc56fa394a5f8495aa3d'
REPLAYER_SHA = 'a352dfe4965efabff55fa53c05a82f7540765ca7dc2c395cd9c7394c5940aa25'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def publishable(data, name):
    for pattern in (rb'OSSAccessKeyId=', rb'X-Amz-Signature=', rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', rb'LTAI[a-zA-Z0-9]{16,}'):
        require(re.search(pattern, data) is None, 'publication credential/signature pattern: ' + name)
    profile_path = Path.home() / '.aliyun/config.json'
    if profile_path.exists():
        for profile in read(profile_path).get('profiles', []):
            for key in ('access_key_id', 'access_key_secret', 'security_token'):
                value = profile.get(key)
                require(not value or value.encode() not in data, 'publication known-credential match: ' + name)
    return data


def check_node(label, node, retention=None):
    required = ['combined-verification.json', 'state.local.json']
    for prefix in ('', 'retry-01/'):
        required += [prefix + name for name in ('verified-download.json', 'checkpoint.local.json',
                                               'results.tar.gz', 'retrieved/results/status.json')]
    missing = [name for name in required if not (node / name).is_file()]
    if missing:
        raise ValueError('pending files: ' + ', '.join(missing))
    combined, state = (read(node / name) for name in required[:2])
    allocation_path = node / 'allocation-record.json'
    allocation = read(allocation_path) if allocation_path.exists() else {
        'run_id': state['run_id'], 'created_resources': state['created_resources']}
    require(combined['all_four_methods_complete'] is True, 'combined verification not complete')
    require(combined['original_attempt_remains_failed'] is True, 'original failure must be preserved')
    require(combined['run_id'] == allocation['run_id'], 'combined inventory identity mismatch')
    cleanup_path = node / 'cleanup.local.json'
    if cleanup_path.exists():
        cleanup = read(cleanup_path)
        require(all(cleanup.get(key) == 0 for key in ('instances_remaining', 'created_disks_remaining',
                                                     'security_groups_remaining', 'buckets_remaining')),
                'exact resource absence is pending')
        require(bool(cleanup.get('finished_utc')) and cleanup.get('bucket_absence_error_code') == 'NoSuchBucket',
                'cleanup timestamp/bucket absence evidence missing')
        require(cleanup.get('preexisting_resources_preserved') is True, 'shared resources preservation missing')
        require(cleanup['exact_created_resources'] == allocation['created_resources'], 'cleanup inventory mismatch')
        release = {'status': 'released', 'evidence': cleanup, 'source_sha256': sha(cleanup_path)}
    elif state.get('cleanup', {}).get('complete') is True:
        cleanup, owned = state['cleanup'], state['created_resources']
        require(cleanup['verified_instances_absent'] == owned['instances'], 'instance absence identity mismatch')
        require(set(cleanup['verified_disks_absent']) == set(owned['disks']), 'disk absence identity mismatch')
        require(cleanup['deleted_security_group'] == owned['security_group'] and cleanup['security_group_absence_verified'] is True,
                'security group absence identity mismatch')
        require(cleanup['deleted_bucket'] == owned['bucket'] and cleanup['bucket_absence_error_code'] == 'NoSuchBucket'
                and cleanup['completed_utc'], 'bucket absence identity mismatch')
        release = {'status': 'released', 'evidence': cleanup, 'source_sha256': sha(node / 'state.local.json'),
                   'source_pointer': '/cleanup', 'inventory_basis': 'original controller state, no separate creation receipt'}
    else:
        require(label == '500g-pl1' and retention is not None, 'resource release is pending')
        retained = read(retention)
        require(retained['root_authorized'] is True and retained['run_id'] == combined['run_id']
                and retained['resource_status'] == 'retained-for-extra-baseline-overlay', 'retention authorization mismatch')
        release = {'status': 'retained-for-extra-baseline-overlay', 'evidence': retained, 'source_sha256': sha(retention)}
    statuses, receipts, sources = {}, {}, {}
    for attempt, path in (('original', node), ('retry-01', node / 'retry-01')):
        receipt, checkpoint = read(path / 'verified-download.json'), read(path / 'checkpoint.local.json')
        require(receipt['run_id'] == combined['run_id'], 'receipt run identity mismatch')
        require(receipt['finalized'] is True and receipt['preempted'] is False, 'finalized archive required')
        require(receipt['known_credential_scan'] == 'passed' and receipt['hits'] == [], 'credential scan not passed')
        archive = path / 'results.tar.gz'
        digest = sha(archive)
        require(digest == receipt['sha256'] == checkpoint['archive']['sha256'] == combined['archives'][attempt]['sha256'],
                'archive SHA mismatch')
        require(archive.stat().st_size == receipt['bytes'] == checkpoint['archive']['bytes'], 'archive size mismatch')
        require(checkpoint['finalized'] is True, 'checkpoint is not terminal')
        status = read(path / 'retrieved/results/status.json')
        with tarfile.open(archive) as stream:
            raw = stream.extractfile('results/status.json')
            require(raw is not None and json.load(raw) == status, 'extracted status differs from archive')
        require(status == checkpoint['status'], 'terminal status/checkpoint mismatch')
        require(status['finished_epoch'] and status['source_archive_sha256'] == SOURCE_SHA, 'status identity incomplete')
        statuses[attempt], receipts[attempt] = status, receipt
        sources[attempt + '-status.json'] = path / 'retrieved/results/status.json'
        sources[attempt + '-receipt.json'] = path / 'verified-download.json'
    require(statuses['original'].get('fatal_error'), 'original failure was removed')
    require(statuses['retry-01'].get('completed_selected_methods') is True and not statuses['retry-01'].get('fatal_error'),
            'retry is incomplete')
    require(tuple(statuses['retry-01']['method_order']) == METHODS[2:], 'retry selected methods changed')
    rows, preparation = [], []
    for name in METHODS:
        attempt = 'original' if name in METHODS[:2] else 'retry-01'
        method = statuses[attempt]['methods'][name]
        require(not method.get('fatal_error') and method['cleanup_errors'] == [], name + ' cleanup/failure')
        require(len(method['methods']) == 1, name + ' report is ambiguous')
        row = next(iter(method['methods'].values()))
        report, command = row['report'], row['replay_command']
        require(row['completed_workload'] is True and report['completed'] is True, name + ' incomplete workload')
        require(command['exit_code'] == 0 and command['timed_out'] is False, name + ' did not exit successfully')
        for field, expected in {'operations': 1747109, 'actual_read_bytes': 281774624,
                                'actual_write_bytes': 194706661, 'remaining_handles': 0}.items():
            require(type(report[field]) is int and report[field] == expected, name + ' workload mismatch: ' + field)
        require(report['mode'] == 'performance' and finite(report['execution_seconds']), name + ' invalid measured duration')
        require(report['valid'] is False, 'preserve diagnostic correctness boundary')
        exits = method.get('mount_exits', {})
        require(bool(exits) and all(items and all(item['exit_code'] == 0 for item in items) for items in exits.values()),
                name + ' natural mount exit pending')
        source = method['source']
        require(source['source_archive_sha256'] == SOURCE_SHA and source['candidate_sha256'] == BREWFS_SHA
                and source['trace_sha256'] == TRACE_SHA and source['baseline_binaries']['full-fuse-replay'] == REPLAYER_SHA,
                name + ' source or binary mismatch')
        require(combined['methods'][name]['report'] == report and combined['methods'][name]['mount_exits'] == exits,
                name + ' combined report mismatch')
        rows.append({'disk_configuration': label, 'method': name, 'attempt': attempt,
                     'execution_seconds': report['execution_seconds'], 'operations': report['operations'],
                     'read_bytes': report['actual_read_bytes'], 'write_bytes': report['actual_write_bytes'],
                     'remaining_handles': report['remaining_handles'], 'expected_errors': report['expected_errors'],
                     'probe_errno_differences': report['probe_errno_differences'], 'valid': report['valid'],
                     'source': 'evidence/' + label + '/' + attempt + '-status.json'})
        base = node if attempt == 'original' else node / 'retry-01'
        for command_path in sorted((base / 'retrieved/results' / name).glob('*.command.json')):
            if any(command_path.name.endswith('-' + suffix + '.command.json') for suffix in ('init', 'copy', 'pack', 'format', 'bucket')):
                value = read(command_path)
                require(value['exit_code'] == 0 and value['timed_out'] is False, 'failed preparation command')
                evidence_name = name + '-' + command_path.name
                sources[evidence_name] = command_path
                preparation.append({'disk_configuration': label, 'method': name, 'stage': command_path.name.removesuffix('.command.json'),
                                    'wall_seconds': value['wall_seconds'], 'source': 'evidence/' + label + '/' + evidence_name})
    node_path = node / 'retrieved/results/node-public.json'
    public = read(node_path)
    require(public['run_id'] == combined['run_id'], 'public node identity mismatch')
    params = public['run_parameters']
    require((params['DataDisk.1.Size'], params['DataDisk.1.PerformanceLevel']) == CONFIGS[label], 'disk configuration mismatch')
    require(params['InstanceType'] == 'ecs.g8i.2xlarge', 'unexpected instance family')
    host_path = node / 'retry-01/retrieved/results/host-identity.json'
    host = read(host_path)
    cpu = [item['data'] for item in host['lscpu']['lscpu'] if item['field'] == 'Model name:']
    require(len(cpu) == 1 and cpu[0], 'actual CPU identity missing')
    sources.update({'node.json': node_path, 'host-identity.json': host_path,
                    'combined-verification.json': node / 'combined-verification.json'})
    return {'label': label, 'rows': rows, 'preparation': preparation, 'cpu': cpu[0],
            'run_id': combined['run_id'], 'parameters': params, 'sources': sources, 'release': release,
            'archive_identity': {name: {'sha256': receipt['sha256'], 'bytes': receipt['bytes']} for name, receipt in receipts.items()}}


def draw(nodes, out, skill_scripts):
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    sys.path.insert(0, str(skill_scripts))
    from audit_panel_alignment import require_matplotlib_panel_alignment
    available = {font.name for font in font_manager.fontManager.ttflist}
    cjk = next((font for font in ('Microsoft YaHei', 'Noto Sans CJK SC', 'SimHei') if font in available), None)
    require(cjk is not None, 'CJK font unavailable')
    mpl.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': [cjk, 'Arial', 'DejaVu Sans'],
        'font.size': 8, 'axes.labelsize': 8, 'xtick.labelsize': 8, 'ytick.labelsize': 8,
        'svg.fonttype': 'none', 'pdf.fonttype': 42, 'axes.unicode_minus': False,
        'axes.spines.top': False, 'axes.spines.right': False, 'axes.linewidth': 0.7,
        'figure.facecolor': 'white', 'savefig.facecolor': 'white', 'legend.frameon': False})
    fig, ax = plt.subplots(figsize=(183 / 25.4, 118 / 25.4))
    fig.subplots_adjust(left=0.31, right=0.97, bottom=0.25, top=0.84)
    palette = ('#92999F', '#527C95', '#C58B59')
    for index, node in enumerate(nodes):
        values = {row['method']: row['execution_seconds'] for row in node['rows']}
        size, level = CONFIGS[node['label']]
        ax.barh([method_index + (index - 1) * 0.23 for method_index in range(4)],
                [values[name] for name in METHODS], height=0.2, color=palette[index],
                label=f'{size} GiB {level}', edgecolor='none')
    ax.set_yticks(range(4), LABELS)
    ax.invert_yaxis()
    ax.set_xlim(left=0)
    ax.set_xlabel('完整回放耗时（秒；越低越短）')
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, color='#E3E6E8', linewidth=0.6)
    ax.tick_params(axis='y', length=0, pad=8)
    fig.legend(loc='upper right', bbox_to_anchor=(0.975, 0.945), ncol=3, fontsize=8)
    fig.text(0.035, 0.98, '四方法 × 三磁盘配置：完整 trace 的单次观测', fontsize=9, va='top')
    fig.text(0.035, 0.15, 'n=1 / 单元；实际 CPU 型号不同（8475B / 8575C），不能归因为容量或 PL 单因素。', fontsize=7, va='top')
    fig.text(0.035, 0.105, '实际 FUSE TTL 可能不同，尚待运行时核实；不能作机制开销因果比较（issue #364）。', fontsize=7, va='top')
    fig.text(0.035, 0.06, '开发诊断；工作量完整不等于严格正确性，C4 尚未通过性能验收。', fontsize=7, va='top')
    fig.canvas.draw()
    require_matplotlib_panel_alignment(fig, json_out=str(out / 'alignment.json'),
        overlay_svg=str(out / 'alignment.svg'), tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True)
    fig.savefig(out / 'replay-seconds.svg')
    fig.savefig(out / 'replay-seconds.pdf')
    fig.savefig(out / 'replay-seconds.png', dpi=600)
    plt.close(fig)


def write_csv(path, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def check_calibration(path, source_root, nodes):
    summary = read(path)
    records = summary['sources']
    # Verify every cited raw file, including the observer evidence kept outside
    # the small public report. The export never silently changes their hashes.
    for record in records.values():
        source = (source_root / record['path']).resolve()
        require(source.is_relative_to(source_root.resolve()), 'calibration source escapes repository')
        require(source.is_file() and source.stat().st_size == record['bytes'] and sha(source) == record['sha256'],
                'calibration source changed: ' + record['path'])
    by_run = {node['run_id']: node for node in summary['nodes']}
    require(set(by_run) == {node['run_id'] for node in nodes}, 'calibration node set mismatch')
    rows = []
    for node in nodes:
        data = by_run[node['run_id']]
        require(data['environment']['cpu_model']['value'] == node['cpu'], 'calibration CPU identity mismatch')
        for case in data['calibration']:
            require(finite(case['iops']['value']) and finite(case['throughput_mib_s']['value']), 'invalid calibration')
            rows.append({'disk_configuration': node['label'], 'case': case['name'], 'kind': case['kind'],
                         'iops': case['iops']['value'], 'throughput_mib_s': case['throughput_mib_s']['value'],
                         'wall_seconds': case['wall_seconds']['value'],
                         'clat_p99_ms': case['completion_only_latency_ms']['p99.000000']['value'],
                         'io_total_p99_ms': case['io_total_latency_ms']['p99.000000']['value']})
        require(len(data['calibration']) == 6 and sum(case['kind'] == 'prefill' for case in data['calibration']) == 1,
                'calibration cases incomplete')
    return {'summary': summary, 'rows': rows, 'source_root': source_root, 'source_sha256': sha(path)}


def export_calibration(calibration, out):
    summary = json.loads(json.dumps(calibration['summary']))
    summary['input_summary_sha256'] = calibration['source_sha256']
    identifiers = {name: 'sha256:' + record['sha256'] for name, record in summary['sources'].items()}
    # Small raw JSON evidence is public; large observer JSONL stays identified
    # by full SHA in the verified source archive, without a broken public link.
    for record in summary['sources'].values():
        original = calibration['source_root'] / record.pop('path')
        if original.suffix == '.json' and original.stat().st_size <= 200000:
            relative = Path('evidence/observation') / (record['sha256'][:16] + '-' + original.name)
            target = out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(publishable(original.read_bytes(), relative.as_posix()))
            record['path'] = relative.as_posix()
            record['public'] = True
        else:
            record['path'] = None
            record['public'] = False
            record['availability'] = 'Full raw observation retained in verified node archive; hash identity only in this compact report.'
    for node in summary['nodes']:
        node.pop('node_directory', None)
    def normalize_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'file_id':
                    value[key] = identifiers[item]
                else:
                    normalize_refs(item)
        elif isinstance(value, list):
            for item in value:
                normalize_refs(item)
    normalize_refs(summary['nodes'])
    summary['sources'] = {identifiers[name]: record for name, record in summary['sources'].items()}
    (out / 'calibration-observation.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    write_csv(out / 'calibration.csv', calibration['rows'])


def extra_accounting_text(preparation, calibration):
    packed = [next(row['wall_seconds'] for row in preparation if row['disk_configuration'] == label
                   and row['stage'].endswith('-pack')) for label in CONFIGS]
    overhead = [method['observer_overhead']['cpu_percent_one_core']['value']
                for node in calibration['summary']['nodes'] for method in node['methods'].values()]
    require(len(overhead) == 12 and all(value is not None for value in overhead), 'all-method observer accounting pending')
    return ('pack／snapshot fixture 构建与上传合并计时依配置顺序为 ' + ' / '.join(f'{value:.3f}' for value in packed)
            + ' 秒，不能进一步拆分；主图仅含回放。12 项观察器自测 CPU 占单核 '
            + f'{min(overhead):.2f}%–{max(overhead):.2f}%，这是开销记账，不是开关观察器的因果对照。')


def redis_note(nodes):
    reductions, increases = [], []
    for node in nodes:
        original = read(node['sources']['original-status.json'])
        retry = read(node['sources']['retry-01-status.json'])
        overlay = next(iter(original['methods']['native-overlay']['methods'].values()))
        packed = next(iter(retry['methods']['native-overlay-pack']['methods'].values()))
        counts = [int(row['redis_after']['total_commands_processed']) - int(row['redis_before']['total_commands_processed'])
                  for row in (overlay, packed)]
        require(all(value > 0 for value in counts), 'Redis instance counter delta unavailable')
        reductions.append(100 * (1 - counts[1] / counts[0]))
        increases.append(100 * (packed['report']['execution_seconds'] / overlay['report']['execution_seconds'] - 1))
    return (f'各节点 packed 相对 overlay 的 Redis 命令计数减少 {min(reductions):.1f}%–{max(reductions):.1f}%，'
            f'回放耗时却增加 {min(increases):.1f}%–{max(increases):.1f}%。这里使用 Redis instance 的 '
            '`total_commands_processed` 前后差，包含监测和其他命令；不能当成单类元数据请求数或缓存因果收益。')


def ttl_review_note():
    return ('**新增源码审查限制（不是已测归因）：**[issue #364](https://github.com/brewfs/brewfs/issues/364)'
            '指出，C4 的 legacy MetaClient 在 ACL capability 为 Unsupported 时可使用 `BREWFS_CACHE_TTL_MS`'
            '（默认 1 s），而 ReadWrite overlay 使用显式配置的 TTL（本实验配置为 0）。'
            '因此 driver 写入 0 不足以保证各方法的有效 FUSE TTL 相同；历史运行未记录该环境变量与实际回复 TTL。'
            '输入约 91% 为 getattr，内核属性缓存可能影响进入用户态的请求数，但本组没有运行时证据量化这一影响，'
            '不能将 native 与 overlay 的全部差值归因于 overlay 机制成本。'
            '原提交 `8a0530d` 是此次源码审查之前的报告快照；本次只补充解释边界，测量数值与原始证据不变。')


def render(nodes, out, skill_scripts, calibration):
    if os.name == 'nt' and not str(out).startswith('\\\\?\\'):
        out = Path('\\\\?\\' + str(out))
    require(not out.exists(), 'Use a new output directory; never overwrite a prior diagnostic')
    out.mkdir(parents=True)
    provenance = {'scope': 'Single trace, n=1 per cell; no causal disk-only or strict correctness claim',
                  'source_archive_sha256': SOURCE_SHA, 'trace_sha256': TRACE_SHA, 'performance_accepted': False,
                  'nodes': {}, 'files': {}, 'rendered_qa': 'pending final PDF/font/collision and visual inspection'}
    provenance['subsequent_source_review'] = {
        'issue': 'https://github.com/brewfs/brewfs/issues/364',
        'pre_review_snapshot_commit': '8a0530d',
        'scope': 'Potential effective FUSE TTL mismatch; runtime environment and reply TTL were not recorded. No measured causal attribution.',
        'measurement_values_changed': False}
    for node in nodes:
        folder = out / 'evidence' / node['label']
        folder.mkdir(parents=True)
        for name, source in node['sources'].items():
            (folder / name).write_bytes(publishable(source.read_bytes(), name))
        (folder / 'resource-status.json').write_text(json.dumps(node['release'], indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        provenance['nodes'][node['label']] = {'run_id': node['run_id'], 'cpu': node['cpu'], 'archives': node['archive_identity'],
                                             'resource_status': node['release']['status']}
    rows = [row for node in nodes for row in node['rows']]
    preparation = [row for node in nodes for row in node['preparation']]
    write_csv(out / 'source-data.csv', rows)
    if preparation:
        write_csv(out / 'preparation.csv', preparation)
    export_calibration(calibration, out)
    draw(nodes, out, skill_scripts)
    lines = ['本图只展示三台新实例四方法的完整回放，**开发诊断，C4 尚未通过性能验收**。', '',
             '![完整回放耗时](replay-seconds.png)', '', '| 方法 | 100 GiB PL0（秒） | 500 GiB PL0（秒） | 500 GiB PL1（秒） |',
             '|---|---:|---:|---:|']
    for name, display in zip(METHODS, LABELS):
        values = [next(row['execution_seconds'] for row in node['rows'] if row['method'] == name) for node in nodes]
        lines.append('| ' + display + ' | ' + ' | '.join(f'{value:.3f}' for value in values) + ' |')
    lines += ['', 'fio 独立校准（每格为 IOPS / MiB·s⁻¹，60 秒 direct I/O；不计入上图）：', '',
              '| 项目 | 100 GiB PL0 | 500 GiB PL0 | 500 GiB PL1 |', '|---|---:|---:|---:|']
    case_names = [('random-read-4k-qd32', '4 KiB 随机读 QD32'), ('random-write-4k-qd32', '4 KiB 随机写 QD32'),
                  ('sequential-read-1m', '1 MiB 顺序读'), ('sequential-write-1m', '1 MiB 顺序写'), ('sync-write-4k', '4 KiB 同步写')]
    for case, display in case_names:
        values = [next(row for row in calibration['rows'] if row['case'] == case and row['disk_configuration'] == node['label']) for node in nodes]
        lines.append('| ' + display + ' | ' + ' | '.join(f"{row['iops']:.1f} / {row['throughput_mib_s']:.2f}" for row in values) + ' |')
    prefill = [next(row['wall_seconds'] for row in calibration['rows'] if row['kind'] == 'prefill' and row['disk_configuration'] == node['label']) for node in nodes]
    lines += ['', '16 GiB 预填充依配置顺序为 ' + ' / '.join(f'{value:.3f}' for value in prefill) + ' 秒，单列准备成本。', '',
              '[校准与观测摘要](calibration-observation.json) · [校准原值 CSV](calibration.csv)。clat p99 不是总延迟；原 fio 未采总延迟分位，保留 null。API Throughput 的 MB/s 标签不冒充实测 MiB/s。']
    lines += ['', extra_accounting_text(preparation, calibration)]
    lines += ['', redis_note(nodes)]
    lines += ['', ttl_review_note()]
    lines += ['', '每项均完成 1,747,109 ops，read 281,774,624 B／write 194,706,661 B，剩余句柄 0，回放及自然挂载退出 0。保留 expected errors 与 probe errno 差异，performance mode 不证明严格正确性。', '',
              '三节点均为 ecs.g8i.2xlarge；实际 CPU：' + '；'.join(node['label'] + ' = ' + node['cpu'] for node in nodes) + '。n=1、宿主型号混杂、顺序执行和未清宿主页缓存限制了容量／PL 因果解释。', '',
              '原 attempt 在 packed 启动前发生端口预检错误；原失败记录保留。仅 packed 与 JuiceFS 在同实例新后端和目录补跑，恢复间隔不计入回放。观察日志都在系统盘；host 身份采集发生在测量开始后，loadavg 不是初始负载。', '',
              '[原始回放数据](source-data.csv) · [准备阶段命令耗时](preparation.csv) · [证据与 SHA](provenance.json)。pack／snapshot fixture 构建上传若为合并计时就不能进一步拆分；本组没有验证在线 freeze／fork／迁移。', '',
              'fio 校准独立于 trace，16 GiB 预填充与五项 60 秒直接 I/O 测量单列；校准与观测摘要另附。资源状态：' +
              '；'.join(node['label'] + (' 已核验实例／两盘／安全组／临时桶释放' if node['release']['status'] == 'released'
                        else ' 为额外 baseline-overlay 验证显式保留，尚未释放') for node in nodes) + '。共享网络由根任务单独管理。']
    (out / 'summary.zh.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    for path in out.rglob('*'):
        if path.is_file():
            provenance['files'][path.relative_to(out).as_posix()] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    (out / 'provenance.json').write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', action='append', required=True, help='100g-pl0=DIR, 500g-pl0=DIR, 500g-pl1=DIR')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--render-authorized', action='store_true', help='Set only after root explicitly authorizes final rendering')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--skill-scripts', type=Path)
    parser.add_argument('--calibration-summary', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True, help='Repository root for checking cited raw-source SHA values')
    parser.add_argument('--retention-evidence', type=Path, help='Explicit root-authorized retention of 500g-pl1 for separate baseline validation; never claims cleanup')
    args = parser.parse_args()
    paths = {}
    for item in args.node:
        label, path = item.split('=', 1)
        require(label in CONFIGS and label not in paths, 'unknown or duplicate node label')
        paths[label] = Path(path).resolve()
    require(set(paths) == set(CONFIGS), 'Exactly the three requested disk configurations are required')
    nodes, pending = [], []
    for label in CONFIGS:
        try:
            nodes.append(check_node(label, paths[label], args.retention_evidence if label == '500g-pl1' else None))
        except (AssertionError, ValueError, KeyError, OSError, tarfile.TarError) as error:
            pending.append({'node': label, 'reason': str(error)})
    if pending:
        print(json.dumps({'status': 'pending', 'issues': pending}, ensure_ascii=False))
        return 2
    common = ('InstanceType', 'ZoneId', 'ImageId', 'SystemDisk.Size', 'SystemDisk.PerformanceLevel')
    require(all(len({node['parameters'][key] for node in nodes}) == 1 for key in common), 'nominal common configuration differs')
    calibration = check_calibration(args.calibration_summary, args.source_root, nodes)
    if args.render:
        require(args.render_authorized and args.out and args.skill_scripts, 'render authorization/output/QA helper required')
        render(nodes, args.out.resolve(), args.skill_scripts.resolve(), calibration)
        print(json.dumps({'status': 'rendered; QA pending', 'out': str(args.out.resolve())}))
    else:
        print(json.dumps({'status': 'ready', 'complete_observations': 12, 'nodes': list(CONFIGS)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
