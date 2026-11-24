"""Build, test and distribute the small-file reader on the existing servers."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, '/srv/ax-cluster/direct/scripts')
import cluster_lib as cl
import suite_volumes as volumes


def main():
    parent = cl.RUN_ROOT / 'functional/c3-reader'
    parent.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='build-', dir=parent))
    source = cl.REPO_ROOT / 'tools/fs-probe/src/bin/c3-loader.rs'
    compiler = '/srv/ax-cluster-20261113/toolchain-1.99.0/bin/rustc'
    binary = out / 'c3-loader'
    with cl.MeasurementLock():
        shutil.copy2(source, out / source.name)
        for extra, target in [(['--test'], out / 'unit-tests'), (['-O'], binary)]:
            subprocess.run([compiler, '--edition=2021', *extra, str(source), '-o', str(target)], check=True)
        tests = subprocess.run([str(out / 'unit-tests')], capture_output=True, text=True, check=True)
        (out / 'unit-tests.stdout').write_text(tests.stdout)
        common = ['load', '--files', '32', '--epochs', '1', '--threads', '2', '--batch', '8',
                  '--seed', '1001', '--verify-bytes', '1']
        positive = out / 'read.json'
        subprocess.run([str(binary), *common, '--root', str(volumes.corpus(32)),
                        '--output', str(positive)], capture_output=True, check=True)
        record = json.loads(positive.read_text())
        if not record['passed'] or record['actual_read_bytes'] != 32 * 4096:
            raise RuntimeError('Small-file reader content or byte count differs')
        missing = out / 'missing'
        missing.mkdir()
        rejected = subprocess.run([str(binary), *common, '--root', str(missing),
                                  '--output', str(out / 'missing.json')], capture_output=True, text=True)
        (out / 'missing.stderr').write_text(rejected.stderr)
        if rejected.returncode == 0 or 'open ' not in rejected.stderr or 'epoch 0 batch' not in rejected.stderr:
            raise RuntimeError('Small-file reader did not report the failing operation and path')
        target = cl.BIN_DIR / 'c3-loader'
        temporary = target.with_suffix('.new')
        shutil.copy2(binary, temporary)
        temporary.chmod(0o755)
        temporary.replace(target)
        for host in cl.ALL_HOST_NAMES:
            if host == 'node-coord':
                continue
            cl.sync_file_to_host(host, binary, str(temporary))
            cl.run_on_host(host, ['python3', '-c',
                'from pathlib import Path; import sys; p=Path(sys.argv[1]); p.chmod(0o755); p.replace(sys.argv[2])',
                str(temporary), str(target)])
        report = {'passed': True, 'source': str(out / source.name), 'binary_bytes': binary.stat().st_size,
                  'actual_read_bytes': record['actual_read_bytes'], 'missing_file_rejected': True,
                  'hosts': cl.ALL_HOST_NAMES, 'completed_at': cl.utc_iso()}
        (out / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
        (cl.RUN_ROOT / 'versions/c3-reader.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
