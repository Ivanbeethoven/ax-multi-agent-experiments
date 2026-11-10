#!/usr/bin/env bash
# Build Linux assets locally before starting any cloud executor.
set -euo pipefail
[[ $# == 2 ]] || { echo 'usage: build-local.sh sources|brewfs|rustfs|tools|manifest ABS_BUILD_ROOT' >&2; exit 2; }
action=$1
build_root=$2
[[ "$build_root" == /* && "$build_root" != / ]] || exit 2
repo_root=${AX_EXPERIMENT_REPO:-$(cd -- "$(dirname -- "$0")/../../../.." && pwd)}
if [[ "${AX_BUILD_SNAPSHOT:-0}" != 1 ]]; then
    mkdir -p "$build_root/scripts"
    script_sha=$(sha256sum "$0" | cut -d' ' -f1)
    snapshot="$build_root/scripts/build-local-$script_sha.sh"
    [[ -e "$snapshot" ]] || cp -- "$0" "$snapshot"
    export AX_EXPERIMENT_REPO="$repo_root" AX_BUILD_SNAPSHOT=1
    exec bash "$snapshot" "$action" "$build_root"
fi
export PATH="$HOME/.cargo/bin:$PATH"
# Use the already installed toolchain; do not let a repository's components
# trigger an unrecorded toolchain upgrade during a long build.
export RUSTUP_TOOLCHAIN=${RUSTUP_TOOLCHAIN:-stable}
export CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-2}
export CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0
mkdir -p "$build_root"/{src,logs,bin,targets,receipts}
attempt_dir="$build_root/logs/$action/$(date -u +%Y%m%dT%H%M%S)-$$"
mkdir -p "$attempt_dir"
printf '%s\n' "$attempt_dir" > "$build_root/receipts/$action.last-attempt"
rustc -Vv > "$attempt_dir/rustc.txt"
cargo -V > "$attempt_dir/cargo.txt"

sync_source() {
    local name=$1 url=$2 branch=$3 reference=$4
    local checkout="$build_root/src/$name"
    if [[ ! -e "$checkout" ]]; then
        local reference_args=()
        [[ ! -d "$reference/.git" ]] || reference_args=(--reference-if-able "$reference" --dissociate)
        git clone "${reference_args[@]}" --single-branch --branch "$branch" "$url" "$checkout"
    fi
    [[ "$(git -C "$checkout" remote get-url origin)" == "$url" ]] || { echo "unexpected $name remote" >&2; exit 1; }
    [[ -z "$(git -C "$checkout" status --porcelain)" ]] || { echo "preserve dirty $name source" >&2; exit 1; }
    [[ "$(git -C "$checkout" branch --show-current)" == "$branch" ]] || exit 1
    git -C "$checkout" fetch --no-tags origin "refs/heads/$branch"
    git -C "$checkout" merge --ff-only FETCH_HEAD
    git -C "$checkout" rev-parse HEAD > "$build_root/receipts/$name.source-commit"
}

case "$action" in
sources)
    sync_source brewfs https://github.com/brewfs/brewfs.git main "${BREWFS_REFERENCE:-/home/hxy/src/brewfs}"
    sync_source rustfs https://github.com/Ivanbeethoven/rustfs.git feat/dynamic-block-size-rebased "${RUSTFS_REFERENCE:-/home/hxy/research/rustfs-ablation}"
    rustc -Vv > "$build_root/receipts/rustc.txt"
    cargo -V > "$build_root/receipts/cargo.txt"
    ;;
brewfs)
    cd "$build_root/src/brewfs"
    [[ -z "$(git status --porcelain)" ]] || exit 1
    export CARGO_TARGET_DIR="$build_root/targets/brewfs"
    features=fuse-io-uring-runtime,workspace-overlay,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
    printf '%s\n' "cargo build --locked --release --no-default-features --bin brewfs --bin packed_v3_snapshot_fixture --example entity_metadata_probe --features $features" > "$attempt_dir/build-command.txt"
    python3 "$repo_root/experiments/unified-suite/pilot-aliyun/scripts/attest-assets.py" capture --build-root "$build_root" --component brewfs > "$attempt_dir/attestation-capture.log"
    cargo build --locked --release --no-default-features --bin brewfs --bin packed_v3_snapshot_fixture --example entity_metadata_probe \
      --features "$features" > "$attempt_dir/build.log" 2>&1
    install -m755 "$CARGO_TARGET_DIR/release/brewfs" "$CARGO_TARGET_DIR/release/packed_v3_snapshot_fixture" "$build_root/bin/"
    install -m755 "$CARGO_TARGET_DIR/release/examples/entity_metadata_probe" "$build_root/bin/"
    python3 "$repo_root/experiments/unified-suite/pilot-aliyun/scripts/attest-assets.py" finish --build-root "$build_root" --component brewfs > "$attempt_dir/attestation-finish.log"
    sha256sum "$build_root/bin/brewfs" "$build_root/bin/packed_v3_snapshot_fixture"
    ;;
rustfs)
    cd "$build_root/src/rustfs"
    [[ -z "$(git status --porcelain)" ]] || exit 1
    export CARGO_TARGET_DIR="$build_root/targets/rustfs"
    printf '%s\n' 'cargo build --locked --release --bin rustfs' > "$attempt_dir/build-command.txt"
    python3 "$repo_root/experiments/unified-suite/pilot-aliyun/scripts/attest-assets.py" capture --build-root "$build_root" --component rustfs > "$attempt_dir/attestation-capture.log"
    cargo build --locked --release --bin rustfs > "$attempt_dir/build.log" 2>&1
    install -m755 "$CARGO_TARGET_DIR/release/rustfs" "$build_root/bin/"
    python3 "$repo_root/experiments/unified-suite/pilot-aliyun/scripts/attest-assets.py" finish --build-root "$build_root" --component rustfs > "$attempt_dir/attestation-finish.log"
    sha256sum "$build_root/bin/rustfs"
    ;;
tools)
    export CARGO_TARGET_DIR="$build_root/targets/ax-tools"
    cargo build --locked --release --manifest-path "$repo_root/tools/fs-probe/Cargo.toml" --bin smallfiles > "$attempt_dir/smallfiles-build.log" 2>&1
    cargo build --locked --release --manifest-path "$repo_root/tools/full-fuse-replay/Cargo.toml" > "$attempt_dir/replay-build.log" 2>&1
    install -m755 "$CARGO_TARGET_DIR/release/smallfiles" "$CARGO_TARGET_DIR/release/full-fuse-replay" "$build_root/bin/"
    sha256sum "$build_root/bin/smallfiles" "$build_root/bin/full-fuse-replay"
    ;;
manifest)
    python3 - "$build_root" <<'PY'
import datetime, hashlib, json, os, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1]); records = []; attestations = []
sources = [('brewfs', 'https://github.com/brewfs/brewfs.git', 'main'),
           ('rustfs', 'https://github.com/Ivanbeethoven/rustfs.git', 'feat/dynamic-block-size-rebased')]
for name, url, branch in sources:
    checkout = root / 'src' / name
    sha = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    latest = subprocess.check_output(['git', 'ls-remote', url, 'refs/heads/' + branch], text=True).split()[0]
    if sha != latest: raise SystemExit(f'{name} branch advanced; rebuild its latest version before deploying')
    if subprocess.check_output(['git','-C',str(checkout),'status','--porcelain'],text=True).strip():
        raise SystemExit(f'{name} source is dirty; preserve it and do not deploy')
    attempt = pathlib.Path((root/'receipts'/(name+'.last-attempt')).read_text().strip())
    proof_path = attempt/'attestation-finished.json'
    proof = json.loads(proof_path.read_text())
    if proof.get('passed') is not True or proof['source']['source_commit'] != sha:
        raise SystemExit(f'{name} completed build attestation does not match its source')
    for output in proof['outputs']:
        with (root/'bin'/output['file']).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != output['sha256']:
                raise SystemExit(f'{name} binary changed after build attestation')
    attestations.append(dict(component=name,path=str(proof_path),sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest()))
    records.append(dict(component=name, repository=url, branch=branch, source_commit=sha))
files = []
for name in ['brewfs', 'packed_v3_snapshot_fixture', 'entity_metadata_probe', 'rustfs', 'smallfiles', 'full-fuse-replay']:
    path = root / 'bin' / name
    with path.open('rb') as stream: sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    files.append(dict(file=name, bytes=path.stat().st_size, sha256=sha))
receipt = dict(format='ax-pilot-local-build-v1', resolved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               sources=records, files=files, build_jobs=os.environ['CARGO_BUILD_JOBS'], release_debug=0,
               build_attestations=attestations,
               cloud_instances_started=False, ready_for_deployment=False,
               next_check='Retain image revision/digest, dependencies and actual native mount qualification')
(root / 'receipts' / 'build-manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
PY
    ;;
*) exit 2 ;;
esac
