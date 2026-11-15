import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tarfile

root = Path('/srv/ax-overlay-merge-20261113-1081395')
assert socket.gethostname() == 'A355'
assert (root / 'ownership.json').is_file()
assert not (root / 'smoke-01.pid').exists()
bundle = root / 'a355-bundle.tar.gz'
with bundle.open('rb') as f:
    digest = hashlib.file_digest(f, 'sha256').hexdigest()
assert digest == '411351fe0f7942fc2c471a7dbaa967b4bb34268eafa5e38f045395081c0e567c', digest
with tarfile.open(bundle, 'r:gz') as f:
    assert all('/' not in m.name and m.isfile() for m in f.getmembers())
    f.extractall(root, filter='data')
with (root / 'launch.log').open('xb') as log:
    p = subprocess.Popen(['bash', str(root / 'a355-smoke.sh')], cwd=root,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
(root / 'smoke-01.pid').write_text(str(p.pid) + '\n')
print(json.dumps(dict(pid=p.pid, bundle_sha256=digest, root=str(root))))
