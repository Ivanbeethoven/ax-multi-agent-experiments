"""Summarize completed local captures without copying or publishing their payloads."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def summarize(folder):
    metrics = json.loads((folder / 'metrics.json').read_text())
    result = json.loads((folder / 'result.json').read_text())
    selection = json.loads((folder / 'selection.json').read_text())
    db = folder / 'replayed/state/data/opencode/opencode.db'
    database = {'present': db.exists()}
    if db.exists():
        # SQLite may recover a WAL or create shared-memory files; inspect a copy.
        database['wal_present'] = db.with_name(db.name + '-wal').exists()
        with tempfile.TemporaryDirectory(prefix='ax-trace-db-check-') as temporary:
            for item in db.parent.glob(db.name + '*'):
                shutil.copyfile(item, Path(temporary) / item.name)
            with sqlite3.connect(Path(temporary) / db.name) as connection:
                database['quick_check'] = connection.execute('pragma quick_check').fetchone()[0]
                database['rows'] = {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                                    for table in ('session', 'message', 'part')}
        database['sha256'] = hashlib.sha256(db.read_bytes()).hexdigest()
        database['matches_captured'] = db.read_bytes() == (folder / 'backing/state/data/opencode/opencode.db').read_bytes()
    (folder / 'session-db-check.json').write_text(json.dumps(database, indent=2))
    capture = folder / 'capture'
    payloads = list((capture / 'blobs').iterdir())
    verification = result['trace_verified']
    if verification['operations'] != metrics['callbacks']:
        raise RuntimeError(f'{folder}: operation count mismatch')
    for name in ('actual_read_bytes', 'actual_write_bytes'):
        if verification[name] != metrics[name]:
            raise RuntimeError(f'{folder}: replay/statistics mismatch: {name}')
    if verification['remaining_handles'] != 0 or not verification['final_tree_equal']:
        raise RuntimeError(f'{folder}: incomplete replay')
    if database['present'] and (database['quick_check'] != 'ok' or not database['matches_captured']):
        raise RuntimeError(f'{folder}: database validation failed')
    return {'directory': str(folder), 'instance_id': selection['instance_id'],
            'agent': json.loads((folder / 'agent-exit.json').read_text()),
            'model_steps': result['model_steps'], 'verification': result['trace_verified'],
            'metrics': metrics, 'database': database,
            'events_bytes': (capture / 'events.jsonl').stat().st_size,
            'events_sha256': digest(capture / 'events.jsonl'),
            'payload_bytes': sum(p.stat().st_size for p in payloads), 'payload_objects': len(payloads)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    records = []
    incomplete = []
    for folder in sorted(args.root.iterdir()):
        if folder.name == 'tooling-snapshot':
            continue
        if folder.is_dir() and (folder / 'result.json').exists():
            records.append(summarize(folder))
        elif folder.is_dir():
            incomplete.append({'directory': str(folder), 'status': 'No completed result; inspect capture and runner logs'})
    totals = {key: sum(record['metrics'][key] for record in records)
              for key in ('callbacks', 'actual_read_bytes', 'actual_write_bytes', 'repeated_read_bytes')}
    totals.update({key: sum(record[key] for record in records)
                   for key in ('events_bytes', 'payload_bytes', 'payload_objects')})
    totals['repeated_read_ratio'] = (totals['repeated_read_bytes'] / totals['actual_read_bytes']
                                     if totals['actual_read_bytes'] else None)
    output = {'purpose': 'Filesystem collection; solution success is not an acceptance condition.',
              'published': False, 'runs': records, 'incomplete': incomplete,
              'totals': totals,
              'tooling_snapshot_note': 'Final scripts at summary time; consult per-run recovery records for changes made during collection.'}
    (args.root / 'summary.json').write_text(json.dumps(output, indent=2))
    source_root = Path(__file__).resolve().parent.parent
    snapshot = args.root / 'tooling-snapshot'
    snapshot.mkdir(exist_ok=True)
    hashes = {}
    sources = list((source_root / 'scripts').glob('*full*trace*.py'))
    sources += [source_root / 'scripts/full_fuse_record.py', source_root / 'scripts/full_trace_common.py',
                source_root / 'scripts/run_swebench_opencode.py', source_root / 'tools/full-fuse-replay/src/main.rs',
                source_root / 'tools/full-fuse-replay/Cargo.lock', source_root / 'tools/full-fuse-replay/Cargo.toml']
    for source in sources:
        relative = source.relative_to(source_root)
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        hashes[str(relative)] = digest(destination)
    (snapshot / 'sha256.json').write_text(json.dumps(hashes, indent=2))
    lines = ['# 本机文件系统采集结果', '', f'原始数据目录（WSL Ubuntu-24.04）：`{args.root.resolve()}`。', '',
             '只验收采集和回放，不按修复成功筛选；本批没有额外运行补丁评分。数据未上传 GitHub。', '',
             '| 任务 | 操作次数 | 读 MiB | 写 MiB | 重复读取比例 | 原始日志 MiB | 写入内容 MiB | 回放 |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for record in records:
        m = record['metrics']
        ratio = m['repeated_read_ratio']
        lines.append(f"| {record['instance_id']} | {m['callbacks']:,} | {m['actual_read_bytes']/2**20:.2f} | "
                     f"{m['actual_write_bytes']/2**20:.2f} | {ratio:.2%} | {record['events_bytes']/2**20:.2f} | "
                     f"{record['payload_bytes']/2**20:.2f} | 通过 |")
    lines += ['', f"已验收 {len(records)} 份记录，共 {totals['callbacks']:,} 次操作；日志与去重后的写入内容合计 {(totals['events_bytes']+totals['payload_bytes'])/2**30:.2f} GiB，不含初始文件和回放副本。", '',
              '读写量是 FUSE 回调实际返回的字节，不是磁盘或云端流量。重复读取按同一文件的未被覆盖区域计算。',
              '记录包括仓库及 HOME/XDG 状态，不包括镜像运行环境、/tmp、网络和内核缓存命中。',
              '目录缓存超时为零，记录器和回放为串行；操作数和运行时间不能直接代表默认生产配置的性能。',
              '每个任务目录保留 initial、capture/events.jsonl、capture/blobs、backing、replayed 和校验结果。',
              'summary.json 包含仓库/状态分项、错误码、数据库检查、日志摘要和未完成目录；tooling-snapshot 保存最终脚本（含收尾修复）。']
    for record in records:
        if record['agent'].get('stop_reason') == 'runtime-limit':
            lines.append(f"{record['instance_id']} 达到一小时运行上限，任务未自然完成；保留从启动到停止的文件系统记录，不计为解题成功。")
        if (Path(record['directory']) / 'runner-recovery.json').exists():
            lines.append(f"{record['instance_id']} 曾发生收尾脚本异常，使用原始记录继续校验；具体原因和丢失的运行信息见其 runner-recovery.json。")
    (args.root / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(output, indent=2))
