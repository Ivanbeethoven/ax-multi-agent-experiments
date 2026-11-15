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
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error('Candidate workers must be between 1 and 16')
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'runner.py').write_bytes(Path(__file__).read_bytes())
    original = cl.generate_mounts_json

    def generate(requests, path):
        result = original(requests, path)
        for record in result['mounts']:
            if record['engine'].startswith('brewfs'):
                record['binary'] = str(args.binary)
                record['environment']['TOKIO_WORKER_THREADS'] = str(args.workers)
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
        result = run_suite.run_io(job, output)
        result['runtime_candidate'] = {'workers': args.workers, 'binary': str(args.binary), 'output': str(output)}
        run_suite.save(output / 'result.json', result)
        results.append(result)
        run_suite.save(args.out / 'summary.json', {'results': results})
        print(json.dumps({'job': identifier, 'workers': args.workers,
                          'seconds': result['timing']['app_done_seconds'],
                          'throughput_mib_s': result['application']['throughput_mib_s']}), flush=True)


if __name__ == '__main__':
    main()
