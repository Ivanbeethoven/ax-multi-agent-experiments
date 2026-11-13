#!/usr/bin/env bash
set -euo pipefail
b=/srv/ax-idcai-20261113
src="$b/src/ax/tools/fs-probe/src/bin"
rustc="$b/toolchain-1.99.0/bin/rustc"
tmp=$(mktemp -d "$b/scratch/smallfiles-checkers.XXXXXX")
trap 'rm -f -- "$tmp/smallfiles-verify.rs" "$tmp/smallfiles-verify" "$tmp/smallfiles-verify-juicefs" "$tmp/smallfiles-copy"; rmdir -- "$tmp"' EXIT
python3 - "$src/smallfiles-verify.rs" "$tmp/smallfiles-verify.rs" <<'PY'
import sys
from pathlib import Path
source, dest = map(Path, sys.argv[1:])
text = source.read_text()
old = 'if names(&root)? != BTreeSet::from(["small".to_owned()]) {'
new = '''let mut root_names = names(&root)?;
    for control in [".accesslog", ".config", ".stats", ".trash"] {
        root_names.remove(control);
    }
    if root_names != BTreeSet::from(["small".to_owned()]) {'''
assert text.count(old) == 1
dest.write_text(text.replace(old, new))
PY
"$rustc" --edition=2021 -O "$src/smallfiles-verify.rs" -o "$tmp/smallfiles-verify"
"$rustc" --edition=2021 -O "$tmp/smallfiles-verify.rs" -o "$tmp/smallfiles-verify-juicefs"
"$rustc" --edition=2021 -O "$src/smallfiles-copy.rs" -o "$tmp/smallfiles-copy"
for name in smallfiles-verify smallfiles-verify-juicefs smallfiles-copy; do
    if [[ -e "$b/bin/$name" ]]; then cmp "$b/bin/$name" "$tmp/$name"
    else install -m755 "$tmp/$name" "$b/bin/$name"; fi
done
sha256sum "$b/bin/"smallfiles-{verify,verify-juicefs,copy} "$src/"smallfiles-{verify,copy}.rs "$tmp/smallfiles-verify.rs"
