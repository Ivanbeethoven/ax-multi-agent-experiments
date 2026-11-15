#!/usr/bin/env bash
set -euo pipefail
revision=${1:?revision}
[[ "$revision" =~ ^[a-f0-9]{40}$ ]]
base=/srv/ax-idcai-20261113
build="$base/builds/brewfs-$revision"
release="$base/releases/brewfs-$revision"
cfg="$base/private/c4-ssh.conf"
test -s "$build/build.json"
jq -e --arg rev "$revision" '.passed and .source_clean and .source_commit==$rev' "$build/build.json" >/dev/null
sha=$(jq -er .archive_sha256 "$build/build.json")
install -d -m700 "$release"
cp -n "$build/binaries.tar.zst" "$build/build.json" "$release/"
bash "$base/install-brewfs-release.sh" "$revision" "$sha"
ssh -F "$cfg" -o BatchMode=yes ax-client2 "install -d -m700 '$release'"
scp -F "$cfg" -B -q "$build/binaries.tar.zst" "$build/build.json" "ax-client2:$release/"
scp -F "$cfg" -B -q "$base/install-brewfs-release.sh" "ax-client2:$base/"
ssh -F "$cfg" -o BatchMode=yes ax-client2 "bash '$base/install-brewfs-release.sh' '$revision' '$sha'"
scp -F "$cfg" -B -q "ax-client2:$release/installed.json" "$build/target-installed.json"
cp "$release/installed.json" "$build/source-installed.json"
