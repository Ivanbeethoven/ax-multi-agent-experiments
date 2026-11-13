#!/usr/bin/env bash
set -euo pipefail
revision=${1:?revision}
expected=${2:?archive checksum}
[[ "$revision" =~ ^[a-f0-9]{40}$ && "$expected" =~ ^[a-f0-9]{64}$ ]]
base=/srv/ax-idcai-20261113
release="$base/releases/brewfs-$revision"
test -d "$release"
cd "$release"
printf '%s  binaries.tar.zst\n' "$expected" | sha256sum -c -
if [[ ! -d bin ]]; then
    mkdir bin
    tar -I zstd -xf binaries.tar.zst -C bin
fi
cd bin
sha256sum -c SHA256SUMS
./brewfs --help > ../brewfs-help.txt
./packed_snapshot_fixture --help > ../producer-help.txt
./brewfs mount --help > ../mount-help.txt
./brewfs workspace fork --help > ../fork-help.txt
for name in juicefs smallfiles smallfiles-verify smallfiles-verify-juicefs smallfiles-copy full-fuse-replay; do
    if [[ -x "$base/bin/$name" && ! -e "$name" ]]; then ln -s "$base/bin/$name" "$name"; fi
done
python3 - "$revision" <<'PY'
import hashlib,json,socket,sys
from pathlib import Path
root=Path.cwd().parent
build=json.loads((root/'build.json').read_text())
assert build['source_commit']==sys.argv[1] and build['source_clean'] and build['passed']
actual={}
for name,row in build['artifacts'].items():
    with (root/'bin'/name).open('rb') as f: actual[name]=hashlib.file_digest(f,'sha256').hexdigest()
    assert actual[name]==row['sha256'], name
value=dict(passed=True,hostname=socket.gethostname(),source_commit=sys.argv[1],artifacts=actual,
           release=str(root),transport='server-to-server WireGuard for target; build-host local install')
(root/'installed.json').write_text(json.dumps(value,indent=2)+'\n')
print(json.dumps(value))
PY
