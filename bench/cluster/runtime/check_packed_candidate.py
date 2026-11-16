"""Check a Packed candidate with four mounts while preserving the suite state."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import sys
import time

PLACEMENTS = (("A366", 45), ("A366", 46), ("A355", 35), ("A355", 36))
PURPOSE = "correctness-with-concurrent-suite"
PORT_CHECK = "import socket,sys; s=socket.socket(); s.bind(('127.0.0.1',int(sys.argv[1]))); s.close()"


def load_runtime():
    sys.path.insert(0, "/srv/ax-cluster/direct/scripts")
    return tuple(importlib.import_module(name) for name in
                 ("cluster_lib", "suite_volumes", "c4_support", "run_c3"))


def run_candidate(name, runtime=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", name):
        raise ValueError("Use a new name with 1–32 letters, digits, underscores or hyphens")
    cl, volumes, support, readers = runtime or load_runtime()
    binary = Path(os.environ["AX_PACKED_BREWFS_BINARY"])
    if not binary.is_absolute():
        raise ValueError("Candidate binary path must be absolute")
    prepared = cl.RUN_ROOT / "prepared/brewfs-packed-on-100000.json"
    volume = json.loads(prepared.read_text())
    if (volume['engine'], volume['dynamic'], volume['files']) != ('brewfs-packed', 'on', 100000):
        raise ValueError("Expected the prepared Dynamic-on 100,000-file Packed dataset")
    out = cl.RUN_ROOT / "diagnostics" / name
    out.mkdir(parents=True, exist_ok=False)
    tag = 'packed-candidate-' + name
    requests = []
    for index, (host, slot) in enumerate(PLACEMENTS):
        if not 1 <= slot <= cl.HOST_BY_NAME[host]['slots']:
            raise ValueError(f"Slot {slot} exceeds {host} capacity")
        requests.append(dict(id=f'{tag}-{index}', phase='measure', engine='brewfs-packed',
            host=cl.HOST_BY_NAME[host]['ssh'], slot=slot, workspace=volume['base'],
            parent_workspace=volume['base'], purpose=PURPOSE, binary=str(binary),
            cache_state='cold', windows=['measure'], root=str(cl.RUN_ROOT), overrides={}))
    generated = cl.generate_mounts_json(requests, out / 'preflight.json')['mounts']
    for request, record, (host, slot) in zip(requests, generated, PLACEMENTS):
        if record['id'] != request['id']:
            raise ValueError('Mount generation changed request order')
        cl.run_on_host(host, ['python3', '-c', PORT_CHECK, str(record['metrics_port'])], log_cmd=False)
        cl.run_on_host(host, ['test', '-x', str(binary)], log_cmd=False)
    state = dict(purpose=PURPOSE, performance_comparison=False, status='running',
        binary=str(binary), prepared=str(prepared), files=100000, epochs=2,
        threads_per_reader=8, reader_count=4, output=str(out),
        started_at=datetime.now(timezone.utc).isoformat())
    suite_status = cl.RUN_ROOT / 'status.json'
    if suite_status.exists():
        state['suite_job_at_start'] = json.loads(suite_status.read_text()).get('current')
    support.save(out / 'run.json', state)
    mounted, tasks = [], defaultdict(list)
    try:
        for index, (host, slot) in enumerate(PLACEMENTS):
            identifier = requests[index]['id']
            item = volumes.init_packed_reader(volume,
                f"{volume['namespace']}_candidate_{name.replace('-', '_')}_{index}",
                out / f'init-{index}')
            mounted.extend(volumes.mount(item, item['base'], identifier, out, host, slot))
        if len(mounted) != 4:
            raise RuntimeError('Expected exactly four candidate mounts')
        epoch = time.time_ns() + 5_000_000_000
        for index, mount in enumerate(mounted):
            output = str(cl.RUN_ROOT / 'hosts' / mount['host_name'] / (mount['id'] + '.json'))
            tasks[mount['host_name']].append(dict(reader_id=mount['id'], host=mount['host_name'],
                slot=mount['slot'], engine=mount['engine'], cgroup=mount['cgroup'],
                metrics_port=mount['metrics_port'], out_path=output, wait_upload=False,
                loader_argv=[str(cl.BIN_DIR / 'c3-loader'), 'load', '--root', mount['mount_directory'],
                    '--output', output, '--files', '100000', '--epochs', '2', '--threads', '8',
                    '--batch', '64', '--seed', str(2026111601 + index * 1009),
                    '--verify-bytes', '1', '--start-unix-ns', str(epoch)]))
        support.save(out / 'tasks.json', tasks)
        results = readers.batch_c3_loader(tasks, tag)
        support.save(out / 'readers.json', results)
        if len(results) != 4 or not all(row['loader']['passed'] and
                row['loader']['read_ops'] == 200000 and
                row['loader']['actual_read_bytes'] == 819200000 for row in results):
            raise RuntimeError('A reader did not complete both byte-verified passes')
        state['status'] = 'passed'
    except BaseException as error:
        state.update(status='failed', error=str(error))
        raise
    finally:
        artifact_errors = []
        for host, rows in tasks.items():
            for row in rows:
                for suffix in ('', '.stdout.log', '.stderr.log'):
                    remote = row['out_path'] + suffix
                    try:
                        response = cl.run_on_host(host, ['cat', remote], check=False, log_cmd=False)
                        if response.returncode == 0:
                            (out / (row['reader_id'] + '.json' + suffix)).write_bytes(response.stdout)
                        else:
                            artifact_errors.append(remote)
                    except Exception as error:
                        artifact_errors.append(dict(path=remote, error=str(error)))
        state['artifact_errors'] = artifact_errors
        try:
            if mounted:
                state['stops'] = cl.stop_mounts(mounted, out, clear_collector=False, remove_cache=True)
                if not all(row.get('unmounted_cleanly') for row in state['stops']):
                    raise RuntimeError('A candidate mount did not exit cleanly')
        except BaseException as error:
            state.update(status='failed', stop_error=str(error))
            raise
        finally:
            state['finished_at'] = datetime.now(timezone.utc).isoformat()
            if suite_status.exists():
                state['suite_job_at_finish'] = json.loads(suite_status.read_text()).get('current')
            support.save(out / 'run.json', state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    print(json.dumps(run_candidate(parser.parse_args().name), indent=2))


if __name__ == '__main__':
    main()
