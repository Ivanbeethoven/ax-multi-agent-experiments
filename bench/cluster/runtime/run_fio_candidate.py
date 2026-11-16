"""Run selected fio conditions with an explicitly recorded BrewFS runtime."""

import argparse
import json
from pathlib import Path

import cluster_lib as cl
import run_suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--job', action='append', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--periodic', choices=['on', 'off'], default='on')
    parser.add_argument('--prefetch-mib', type=int, choices=[4, 16, 32, 64])
    parser.add_argument('--prefetch-workers', type=int, choices=[4, 8, 16])
    parser.add_argument('--read-fetch-policy', choices=['auto', 'whole-object', 'sparse'])
    parser.add_argument('--read-memory-mib', type=int, choices=[512, 4096])
    parser.add_argument('--input-write-memory-mib', type=int, choices=[256, 512, 1024])
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error('Candidate workers must be between 1 and 16')
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'runner.py').write_bytes(Path(__file__).read_bytes())
    original = cl.generate_mounts_json

    def generate(requests, path):
        result = original(requests, path)
        input_ids = {request['id'] for request in requests if request['purpose'] == 'input'}
        for record in result['mounts']:
            if record['engine'].startswith('brewfs'):
                record['binary'] = str(args.binary)
                record['environment']['TOKIO_WORKER_THREADS'] = str(args.workers)
                if args.input_write_memory_mib is not None and record['id'] in input_ids:
                    write_bytes = args.input_write_memory_mib * 1024**2
                    record['capacity_reservation_bytes'] += write_bytes - record['cache']['write_memory_bytes']
                    record['cache']['write_memory_bytes'] = write_bytes
                    record['cache']['memory_budget_bytes'] = write_bytes
                if args.read_fetch_policy is not None:
                    record['cache']['read_fetch_policy'] = args.read_fetch_policy
                if args.read_memory_mib is not None:
                    record['capacity_reservation_bytes'] += args.read_memory_mib * 1024**2 - record['cache']['read_memory_bytes']
                    record['cache']['read_memory_bytes'] = args.read_memory_mib * 1024**2
                if args.prefetch_mib is not None:
                    record['cache']['prefetch_max_bytes'] = args.prefetch_mib * 1024**2
                if args.prefetch_workers is not None:
                    record['cache']['prefetch_concurrency'] = args.prefetch_workers
            else:
                if args.prefetch_mib is not None:
                    record['options']['max-readahead'] = str(args.prefetch_mib) + 'M'
                if args.prefetch_workers is not None:
                    record['options']['max-downloads'] = str(args.prefetch_workers)
            if args.prefetch_mib is not None:
                record['prefetch']['data_window_bytes'] = args.prefetch_mib * 1024**2
            if args.prefetch_workers is not None:
                record['prefetch']['data_workers'] = args.prefetch_workers
                record['download_workers'] = args.prefetch_workers
        run_suite.save(path, result)
        return result

    cl.generate_mounts_json = generate
    plan = json.loads((cl.RUN_ROOT / 'plan.json').read_text())
    jobs = {job['id']: job for job in plan['jobs'] if job['group'] == '01-io'}
    results = []
    for identifier in args.job:
        job = dict(jobs[identifier], id=identifier + '-workers-' + str(args.workers))
        output = args.out / identifier / 'attempt-01'
        output.mkdir(parents=True)
        run_suite.save(output / 'job.json', job)
        run_suite.save(output / 'source-record.json', json.loads((cl.REPO_ROOT / 'source-record.json').read_text()))
        paused_hosts = []
        try:
            if args.periodic == 'off':
                for host in cl.ALL_HOST_NAMES:
                    state = cl.collector_rpc(host, {'action': 'status'})
                    if not state.get('paused'):
                        cl.collector_rpc(host, {'action': 'pause'})
                        paused_hosts.append(host)
            result = run_suite.run_io(job, output)
        finally:
            for host in paused_hosts:
                cl.collector_rpc(host, {'action': 'resume'})
        result['runtime_candidate'] = {'workers': args.workers if job['parameters']['system'].startswith('brewfs') else None,
                                      'binary': str(args.binary) if job['parameters']['system'].startswith('brewfs') else None,
                                      'input_write_memory_mib': args.input_write_memory_mib if job['parameters']['system'].startswith('brewfs') else None,
                                      'periodic_sampling': args.periodic, 'output': str(output)}
        run_suite.save(output / 'result.json', result)
        results.append(result)
        run_suite.save(args.out / 'summary.json', {'results': results})
        print(json.dumps({'job': identifier, 'workers': args.workers,
                          'seconds': result['timing']['app_done_seconds'],
                          'throughput_mib_s': result['application']['throughput_mib_s']}), flush=True)


if __name__ == '__main__':
    main()
