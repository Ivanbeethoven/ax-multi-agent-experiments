"""Fork, read-only Packed, and multi-agent trace workloads."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import random
import time

import cluster_lib as cl
import c4_support as support
import run_c2 as replay
import run_c3 as readers
import rustfs_layout_tools as layout
import suite_volumes as volumes
from run_suite import save, select_backend, SYSTEMS


def workload(mount, action, out, files, changes=None):
    arguments = ['python3', '-m', 'bench.cluster.c4_workload', action,
        '--root', mount['mount_directory'], '--files', str(files), '--file-bytes', '4096']
    if changes:
        cl.sync_file_to_host(mount['host_name'], changes, str(changes))
        arguments += ['--changes', str(changes)]
    result = support.command(arguments, out, host=mount['host_name'])
    return json.loads(result.stdout)


def run_fork(job, out, small=False):
    p = job['parameters']
    nodes = select_backend(True, out)
    files = 32 if small else p['files']
    system = SYSTEMS[p['system']]
    direct = p['mode'] == 'direct-parent'
    parent = support.prepare_parent(system, files, p['file_bytes'], direct=direct,
                                    tag_suffix='-direct' if direct else '')
    save(out / 'parent.json', parent)
    layout.evict_bucket_page_cache(nodes, [parent['bucket']])
    before = cl.collector_mark_all(job['id'], 'fork-start')
    if direct:
        children, fork_seconds = [parent['base']], 0.0
    else:
        children, fork_seconds = support.fork(parent, min(p['children'], 2) if small else p['children'],
                                              job['id'] + '-' + out.name, out / 'fork')
    after = cl.collector_mark_all(job['id'], 'fork-end')
    save(out / 'fork-start.json', before)
    save(out / 'fork-end.json', after)
    requests, specs = [], {}
    for index, (workspace, (host, slot)) in enumerate(zip(children, support.placements(len(children)))):
        identifier = f'{job["id"]}-child-{index:03d}'
        requests.append(support.request(parent, workspace, identifier, host, slot))
        specs[identifier] = support.spec(parent, workspace, cl.endpoint_for_slot(slot))
    config = out / 'mounts.json'
    cl.generate_mounts_json(requests, config)
    mounted = replay.batch_start_mounts(config, specs, job['id'])
    changes_file = out / 'changes.json'
    count = min(files, p['overwrite_files'])
    from bench.cluster.c4_workload import make_changes
    save(changes_file, make_changes(count, kind='range-overwrite', seed=job['seed']))
    result = {'fork_seconds': fork_seconds, 'children': len(children),
              'fork_backend_delta': cl.summarize_phase_delta(before, after),
              'mount_ready_seconds': [m['mount_ready_seconds'] for m in mounted],
              'parent_prepare_seconds': 0.0 if not direct and system in ('brewfs-native-overlay', 'juicefs-overlay') else parent['prepare_seconds']}
    try:
        if p['read_files']:
            first = cl.collector_mark_all(job['id'], 'read-start')
            result['read'] = workload(mounted[0], 'read', out / 'read', min(files, p['read_files']))
            second = cl.collector_mark_all(job['id'], 'read-end')
            result['read_backend_delta'] = cl.summarize_phase_delta(first, second)
            result['write'] = workload(mounted[0], 'apply', out / 'write', files, changes_file)
            result['upload'] = cl.wait_uploads(mounted)
            third = cl.collector_mark_all(job['id'], 'write-end')
            result['write_backend_delta'] = cl.summarize_phase_delta(second, third)
            save(out / 'read-start.json', first)
            save(out / 'read-end.json', second)
            save(out / 'write-end.json', third)
        else:
            with ThreadPoolExecutor(max_workers=6) as workers:
                list(workers.map(lambda m: workload(m, 'first-read', out / (m['id'] + '-read'), files), mounted))
    finally:
        result['stops'] = replay.batch_stop_mounts(mounted, job['id'], remove_cache=True)
    # Remount the first child on another host and compare the actual file contents.
    source = mounted[0]['host_name']
    target = 'A355' if source != 'A355' else 'A366'
    checked = support.mount_one(parent, children[0], job['id'] + '-verify', out, host=target, phase='verify')
    try:
        result['verification'] = workload(checked[0], 'verify' if p['read_files'] else 'first-read',
                                          out / 'verify', files, changes_file if count else None)
    finally:
        cl.stop_mounts(checked, out, clear_collector=False, remove_cache=True)
    if not direct and count:
        checked = support.mount_one(parent, parent['base'], job['id'] + '-parent-check', out)
        try:
            result['parent_verification'] = workload(checked[0], 'verify', out / 'parent-verify', files)
        finally:
            cl.stop_mounts(checked, out, clear_collector=False, remove_cache=True)
    return result


def run_packed(job, out, small=False, prepared_path=None):
    p = job['parameters']
    nodes = select_backend(p['dynamic'] == 'on', out)
    files = 32 if small else p['files']
    output_tag = out.relative_to(cl.RUN_ROOT).as_posix().replace('/', '-')
    key = f'{p["system"]}-{p["dynamic"]}-{files}'
    registry = cl.RUN_ROOT / 'prepared' / (key + '.json')
    if p['phase'] == 'prepare':
        if prepared_path is None:
            volume = volumes.prepare_dataset(p['system'], p['dynamic'], files, out)
        else:
            volume = json.loads(Path(prepared_path).read_text())
            for field, expected in [('system', p['system']), ('dynamic', p['dynamic']), ('files', files)]:
                if volume.get(field) != expected:
                    raise ValueError('Prepared volume differs in ' + field)
            volume['prepared_source'] = str(prepared_path)
            save(out / 'volume.json', volume)
        volume['layout'] = layout.inspect_bucket_layout(nodes, volume['bucket'])
        verify_mount = volumes.mount(volume, volume['base'], job['id'] + '-' + out.name + '-verify', out, host='A355')
        try:
            remote = str(cl.RUN_ROOT / 'hosts/A355' / (output_tag + '-verify.json'))
            support.command([str(cl.BIN_DIR / 'c3-loader'), 'load', '--root', verify_mount[0]['mount_directory'],
                '--output', remote, '--files', str(files), '--epochs', '1', '--threads', '2',
                '--batch', '64', '--seed', str(job['seed']), '--verify-bytes', '1'], out / 'verify', host='A355')
            volume['verification'] = json.loads(cl.run_on_host('A355', ['cat', remote]).stdout)
        finally:
            cl.stop_mounts(verify_mount, out, clear_collector=False, remove_cache=True)
        if p['system'] != 'brewfs-native-tikv' or volume.get('revision'):
            save(registry, volume)
        return volume
    if p['system'] == 'brewfs-native-tikv' and not registry.is_file():
        raise FileNotFoundError(f'Register the existing Native reader parent in {registry}')
    volume = json.loads(registry.read_text())
    if p['system'] == 'brewfs-native-tikv' and not volume.get('revision'):
        raise ValueError(f'Missing Native reader revision in {registry}')
    count = 2 if small else p['readers']
    layout.evict_bucket_page_cache(nodes, [volume['bucket']])
    placements = support.placements(count)
    mounted = []
    try:
        native_children = []
        if volume['engine'] == 'brewfs-native-overlay':
            native_children = volumes.admin(volume, ['fork-shared-bucket', '--revision', volume['revision'],
                                '--count', str(count)], out / 'fork-readers')['workspaces']
        for index, (host, slot) in enumerate(placements):
            item = volume
            if volume['engine'] == 'brewfs-packed':
                attempt = out.name.replace('-', '_')
                namespace = f'{volume["namespace"]}_reader_{job["configuration"]}_{index}_{attempt}'
                item = volumes.init_packed_reader(volume, namespace, out / f'init-{index}')
            elif volume['engine'] == 'brewfs-native-overlay':
                item = dict(volume)
                item['base'] = native_children[index]
            mounted.extend(volumes.mount(item, item['base'], f'{job["id"]}-{out.name}-reader-{index}', out, host, slot,
                           {'packed_cache': p.get('packed_cache', 'on')} if volume['engine'] == 'brewfs-packed' else {}))
        per_host = defaultdict(list)
        for m in mounted:
            per_host[m['host_name']].append(m)
        cl.collector_set_mounts(per_host)
        tasks = defaultdict(list)
        epoch = time.time_ns() + 5_000_000_000
        for index, m in enumerate(mounted):
            output = str(cl.RUN_ROOT / 'hosts' / m['host_name'] / (output_tag + '-' + m['id'] + '.json'))
            tasks[m['host_name']].append({'reader_id': m['id'], 'host': m['host_name'], 'slot': m['slot'],
                'engine': m['engine'], 'cgroup': m['cgroup'], 'metrics_port': m['metrics_port'],
                'out_path': output, 'wait_upload': False, 'loader_argv': [str(cl.BIN_DIR / 'c3-loader'),
                    'load', '--root', m['mount_directory'], '--output', output, '--files', str(files),
                    '--epochs', str(p['epochs']), '--threads', '2', '--batch', '64',
                    '--seed', str(job['seed'] + index * 1009), '--verify-bytes', '1', '--start-unix-ns', str(epoch)]})
        before = cl.collector_mark_all(job['id'], 'read-start')
        save(out / 'read-start.json', before)
        start = time.monotonic()
        try:
            results = readers.batch_c3_loader(tasks, job['id'])
            seconds = time.monotonic() - start
        finally:
            after = cl.collector_mark_all(job['id'], 'read-end')
            save(out / 'read-end.json', after)
        save(out / 'read-start.json', before)
        save(out / 'read-end.json', after)
        save(out / 'readers.json', results)
        result = {'seconds': seconds, 'readers': results,
                'backend_delta': cl.summarize_phase_delta(before, after), 'volume': volume}
    finally:
        stops = replay.batch_stop_mounts(mounted, job['id'], remove_cache=True)
        save(out / 'reader-stops.json', stops)
    result['stops'] = stops
    return result


def schedule(task, agents, seed, out):
    source = json.loads((cl.INPUT_DIR / 'traces' / task['case'] / 'boundaries.json').read_text())
    if source.get('reviewed') is not True:
        raise ValueError('Tool boundaries have not been reviewed: ' + task['case'])
    rng = random.Random(seed)
    remaining = len(source['boundaries']) * 4000
    waits = []
    for index in range(len(source['boundaries'])):
        left = len(source['boundaries']) - index - 1
        delay = rng.randint(max(1000, remaining - left * 7000), min(7000, remaining - left * 1000))
        waits.append(delay)
        remaining -= delay
    rng.shuffle(waits)
    rows = [dict(row, delay_ms=delay) for row, delay in zip(source['boundaries'], waits)]
    plan = {'schema': 'trace-think-plan-v2', 'simulate_llm': True,
        'algorithm': 'python-random-MT19937-feasible-allocation-shuffle-v2',
        'agent_count': agents, 'agent_id': task['task_id'], 'seed': seed,
        'min_interval_ms': 1000, 'max_interval_ms': 7000, 'total_think_ms': sum(waits),
        'events_operations': source['events_operations'], 'events_bytes': source['events_bytes'],
        'evidence': source['evidence'], 'boundaries': rows}
    save(out, plan)
    cl.sync_file_to_host(task['host'], out, str(out))
    return str(out)


def run_replay(job, out, small=False):
    p = job['parameters']
    select_backend(p['dynamic'] == 'on', out)
    original_cases = replay.CASES
    if small:
        replay.CASES = ('office-excel-01',)
    policy = ('dynamic' if p['dynamic'] == 'on' else 'official') + ('-check' if small else '')
    try:
        bases = replay.ensure_c2_bases(policy, SYSTEMS[p['system']])
        cases = list(replay.CASES)
        agents = 1 if small else p['agents']
        rng = random.Random(job['seed'])
        rng.shuffle(cases)
        placements = None
        verify_hosts = None
        truncate_seconds = None
        placement_file = cl.RUN_ROOT / 'replay-placement.json'
        if not small and placement_file.exists():
            profile = json.loads(placement_file.read_text())
            if str(agents) in profile['counts']:
                counts = profile['counts'][str(agents)]
                hosts = profile['hosts']
                verify_hosts = profile['verify_hosts']
                if p['system'] == 'brewfs-native':
                    truncate_seconds = profile.get('brewfs_truncate_flush_seconds')
                    if isinstance(truncate_seconds, dict):
                        truncate_seconds = truncate_seconds[str(agents)]
                if (len(set(hosts)) != len(hosts) or len(counts) != len(hosts)
                        or sum(counts) != agents or set(verify_hosts) != set(hosts)
                        or set(verify_hosts.values()) != set(hosts)):
                    raise ValueError('Invalid replay placement')
                placements = []
                for host, count in zip(hosts, counts):
                    target = verify_hosts[host]
                    if (host == target or type(count) is not int or count < 0
                            or count > min(cl.HOST_BY_NAME[h]['active'] for h in (host, target))):
                        raise ValueError(f'Invalid measurement or verification slots for {host}')
                    placements.extend((host, slot) for slot in range(1, count + 1))
                save(out / 'replay-placement.json', profile)
        if placements is None:
            placements = [('A366', 1)] if agents == 1 else support.placements(agents)
        tasks = []
        for index, (host, slot) in enumerate(placements):
            task = {'task_id': f'a{index+1:03d}', 'case': cases[index % len(cases)],
                'host': host, 'ssh': cl.HOST_BY_NAME[host]['ssh'], 'slot': slot,
                'start_delay_ms': 0 if agents == 1 else rng.randrange(30001)}
            if verify_hosts is not None:
                task['verify_host'] = verify_hosts[host]
            task['pacing_path'] = schedule(task, agents, job['seed'] + 100003 * (index+1),
                                            out / 'pacing' / (task['task_id'] + '.json'))
            tasks.append(task)
        save(out / 'tasks-plan.json', tasks)
        directory = out / 'batch-01'
        directory.mkdir()
        condition = {'id': job['id'] + '-' + out.name + '-b1', 'family': 'trace', 'kind': 'measurement',
            'parameters': {'agents': agents, 'pacing': 'short', 'writes': 'remote-ready'}}
        cohort = {'seed': job['seed'], 'shared_tasks': tasks}
        if truncate_seconds is not None:
            condition['parameters']['truncate_flush_seconds'] = truncate_seconds
        result = replay.execute_c2_cohort_arm(condition, SYSTEMS[p['system']], job['repeat'],
                                              cohort, bases, directory)
        save(directory / 'result.json', result)
        return {'agents': agents, 'completed_tasks': len(tasks), 'batches': [result],
            'replay_seconds': result['timing']['app_done_seconds'],
            'actual_read_bytes': result['metrics']['actual_read_bytes'],
            'actual_write_bytes': result['metrics']['actual_write_bytes'],
            'operations': result['metrics']['trace_operations']}
    finally:
        replay.CASES = original_cases
