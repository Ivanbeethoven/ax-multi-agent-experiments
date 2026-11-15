#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
if ! command -v protoc >/dev/null 2>&1 || ! command -v clang >/dev/null 2>&1; then
    echo "[$(date -Is)] Installing build dependencies on VM..."
    apt-get update -q
    apt-get install -y -q --no-install-recommends \
        build-essential pkg-config libssl-dev libfuse3-dev protobuf-compiler \
        cmake clang libclang-dev llvm-dev git curl ca-certificates
fi

if ! swapon --show | grep -q '/data/rustfs0/swapfile'; then
    echo "[$(date -Is)] Creating temporary 16G swapfile on NVMe for build safety..."
    fallocate -l 16G /data/rustfs0/swapfile
    chmod 600 /data/rustfs0/swapfile
    mkswap /data/rustfs0/swapfile
    swapon /data/rustfs0/swapfile
fi

export HOME=/root
export CARGO_HOME=/data/rustfs0/cargo-home
export RUSTUP_HOME=/data/rustfs0/rustup-home
export PATH="$CARGO_HOME/bin:$PATH"

if [[ ! -x "$CARGO_HOME/bin/cargo" ]]; then
    echo "[$(date -Is)] Installing Rust toolchain on NVMe..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
fi
rustc --version
cargo --version

SRC_DIR=/data/rustfs0/brewfs-src
export CARGO_TARGET_DIR=/data/rustfs0/brewfs-target
if [[ -d "$SRC_DIR/.git" ]]; then
    echo "[$(date -Is)] Updating existing $SRC_DIR (preserving target cache)..."
    git -C "$SRC_DIR" fetch origin exp/c1-g-ablations
    git -C "$SRC_DIR" reset --hard origin/exp/c1-g-ablations
else
    echo "[$(date -Is)] Cloning marshawcoco/brewfs (branch exp/c1-g-ablations)..."
    git clone --depth 1 -b exp/c1-g-ablations https://github.com/marshawcoco/brewfs.git "$SRC_DIR"
fi
cd "$SRC_DIR"
git log -1 --oneline

# Use rlib only + debug=false for fast linking without unnecessary cdylib/staticlib
python3 - <<'PY'
from pathlib import Path
p = Path("Cargo.toml")
txt = p.read_text(encoding="utf-8")
txt = txt.replace('crate-type = ["rlib", "cdylib", "staticlib"]', 'crate-type = ["rlib"]')
txt = txt.replace('[profile.release]\ndebug = true\nstrip = false', '[profile.release]\ndebug = false\nstrip = true')
txt = txt.replace('[profile.release]\r\ndebug = true\r\nstrip = false', '[profile.release]\r\ndebug = false\r\nstrip = true')
p.write_text(txt, encoding="utf-8")
PY

mkdir -p /mnt/slayerfs/results/retest-fixed

echo "[$(date -Is)] Building release brewfs binary on 8-core NVMe (lean feature set)..."
cargo build --release --bin brewfs \
    --no-default-features \
    --features fuse-io-uring-runtime,rustfs-ec-block-size-hint,observability

install -m 755 "$CARGO_TARGET_DIR/release/brewfs" /mnt/slayerfs/bin/brewfs
ls -lh /mnt/slayerfs/bin/brewfs
sha256sum /mnt/slayerfs/bin/brewfs /mnt/slayerfs/bin/rustfs | tee /mnt/slayerfs/results/retest-fixed/binary-sha256.txt

echo "[$(date -Is)] Running targeted unit tests (--release)..."
cargo test --release --lib --no-default-features --features fuse-io-uring-runtime,rustfs-ec-block-size-hint,observability \
    cadapter::s3::tests::rustfs_ec_hint_read_preference_keeps_legacy_controls_matched \
    -- --nocapture | tee /mnt/slayerfs/results/retest-fixed/unit-tests-s3-hint.log

cargo test --release --lib --no-default-features --features fuse-io-uring-runtime,rustfs-ec-block-size-hint,observability \
    chunk::store::read_amplification_tests \
    -- --nocapture | tee /mnt/slayerfs/results/retest-fixed/unit-tests-read-amp.log

cargo test --release --lib --no-default-features --features fuse-io-uring-runtime,rustfs-ec-block-size-hint,observability \
    vfs::io::writer::coalesce_tests \
    -- --nocapture | tee /mnt/slayerfs/results/retest-fixed/unit-tests-writer-coalesce.log

cargo test --release --lib --no-default-features --features fuse-io-uring-runtime,rustfs-ec-block-size-hint,observability \
    vfs::io::reader::tests::test_submit_prefetch \
    -- --nocapture | tee /mnt/slayerfs/results/retest-fixed/unit-tests-reader-prefetch.log

echo "[$(date -Is)] Cleaning temporary build directory and swapfile on /data/rustfs0 before perf runs..."
cd /mnt/slayerfs
swapoff /data/rustfs0/swapfile || true
rm -rf /data/rustfs0/swapfile /data/rustfs0/brewfs-src /data/rustfs0/brewfs-target /data/rustfs0/cargo-home /data/rustfs0/rustup-home

echo "[$(date -Is)] Starting focused C1 G-Ablations + Coldread A/B re-test..."
python3 /mnt/slayerfs/run_retest_fixed.py
echo "[$(date -Is)] ALL DONE!"
