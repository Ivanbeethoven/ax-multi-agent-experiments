"""Copy experiment code to the coordinator using its configured SSH alias."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='idcai-60001')
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ax-suite-') as temporary:
        archive = Path(temporary) / 'suite.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            for directory in ('bench', 'configs', 'docs', 'experiments', 'infra'):
                for source in sorted((ROOT / directory).rglob('*')):
                    if source.is_file() and '__pycache__' not in source.parts and source.suffix in ('.py', '.json', '.md'):
                        bundle.add(source, arcname=source.relative_to(ROOT).as_posix())
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            changed = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
            record = json.dumps({'repository': 'Ivanbeethoven/ax-multi-agent-experiments',
                                 'commit': revision, 'working_tree_changes': changed}).encode()
            member = tarfile.TarInfo('source-record.json')
            member.size = len(record)
            bundle.addfile(member, io.BytesIO(record))
        subprocess.run(['scp', str(archive), options.host + ':/srv/ax-cluster/direct/suite.tar.gz'], check=True)
    source = '''
import json,shutil,tarfile
from pathlib import Path
root=Path('/srv/ax-cluster/direct/repo');root.mkdir(parents=True,exist_ok=True)
with tarfile.open('/srv/ax-cluster/direct/suite.tar.gz') as bundle:bundle.extractall(root,filter='data')
context=root/'configs/runtime.json'
target=Path('/srv/ax-cluster/direct/runtime/context.json');target.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(context,target)
for source in (root/'bench/cluster/runtime').glob('*.py'):shutil.copy2(source,Path('/srv/ax-cluster/direct/scripts')/source.name)
run=Path('/srv/ax-cluster/runs/direct')/json.loads(context.read_text())['run_id']
run.mkdir(parents=True,exist_ok=True)
print(run)
'''
    subprocess.run(['ssh', options.host, 'python3 -'], input=source.encode(), check=True)


if __name__ == '__main__':
    main()
