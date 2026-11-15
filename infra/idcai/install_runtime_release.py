"""Install a completed BrewFS CLI build through the coordinator's SSH connections."""

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, '/srv/ax-cluster/direct/scripts')
import cluster_lib as cl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--build-record', type=Path, required=True)
    args = parser.parse_args()
    record = json.loads(args.build_record.read_text())
    if not record.get('passed'):
        raise ValueError('Build record must report successful compilation and tests')
    revision = record['source_revision']
    if len(revision) != 40 or not all(c in '0123456789abcdef' for c in revision):
        raise ValueError('A complete source revision is required')
    release = Path('/srv/ax-cluster/releases') / revision[:8]
    release.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.binary, release / 'brewfs')
    shutil.copy2(args.build_record, release / 'build.json')
    records = {}
    for host in cl.ALL_HOST_NAMES:
        cl.run_on_host(host, ['mkdir', '-p', str(release)])
        if host != 'A366':
            cl.sync_file_to_host(host, release / 'brewfs', str(release / 'brewfs'))
            cl.sync_file_to_host(host, release / 'build.json', str(release / 'build.json'))
        script = '''import pathlib,shutil,subprocess,json
release=pathlib.Path(RELEASE)
target=pathlib.Path('/srv/ax-cluster/direct/bin/brewfs')
temporary=target.with_name('brewfs.incoming')
shutil.copy2(release/'brewfs',temporary)
temporary.chmod(0o755)
subprocess.run([str(temporary),'--version'],check=True,stdout=subprocess.DEVNULL)
temporary.replace(target)
shutil.copy2(release/'build.json',target.with_name('brewfs.build.json'))
print(json.dumps({'binary':str(target),'bytes':target.stat().st_size,'build':json.loads((release/'build.json').read_text())}))
'''.replace('RELEASE', repr(str(release)))
        response = cl.run_on_host(host, ['python3', '-'], input_bytes=script.encode())
        records[host] = json.loads(response.stdout)
        if records[host]['bytes'] != args.binary.stat().st_size:
            raise ValueError('Installed binary size differs on ' + host)
        print(json.dumps({'host': host, 'installed_revision': revision}), flush=True)
    output = cl.RUN_ROOT / 'versions' / ('brewfs-' + revision[:8] + '.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'build': record, 'hosts': records}, indent=2) + '\n')


if __name__ == '__main__':
    main()
