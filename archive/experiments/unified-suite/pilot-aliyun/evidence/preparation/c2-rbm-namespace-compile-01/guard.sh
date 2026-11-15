#!/bin/bash
# Isolated C2 correctness build, using offline Cargo and an independent target.
set -euo pipefail
umask 077
prior=/home/dev/ax-brewfs-c4-build-20261112.GWrxnDJ8
prior_unit=ax-brewfs-c4-native-fork-gwrxndj8-resume-01.service
prior_invocation=15572fd1d92a48e0ace14fe5df2f84b4
prior_bin=a47eecbded65690bfd05673ce66c32384a815503ea191b1e3cb761fa42f8f529
unit=ax-brewfs-c2-hot-namespace-a34cb1ba-01.service
base=ca07159f3da4b670e4aeb878faef928e69bc1634
tree=1ee7c97a427156d9c2cfe056f857e2e01efeb714
patch=a34cb1ba0c94887368eaec85ae855d4766a39e29f38a023200525cf72fac2b40
features=fuse-io-uring-runtime,workspace-overlay,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
new_paths=(src/workspace_overlay/meta_layer/packed_read_control.rs src/workspace_overlay/packed_v3/wire005/payload_retention.rs)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_OPTIONAL_LOCKS=0 GIT_ALLOW_PROTOCOL=file
refuse() { printf 'Refused: %s\n' "$*" >&2; exit 70; }
validate_checkout() {
    cd -- "$job/source"
    test "$(git rev-parse HEAD)" = "$base" || refuse 'BrewFS commit differs'
    test "$(git rev-parse 'HEAD^{tree}')" = "$tree" || refuse 'BrewFS source tree differs'
    git diff --cached --quiet HEAD || refuse 'BrewFS index differs'
    test "$(git ls-files --others --exclude-standard)" = "$(printf '%s\n' "${new_paths[@]}")" || refuse 'New source paths differ'
    git diff --check
    git apply --reverse --check "$job/candidate.patch"
    actual=$(
        {
            git diff --binary --no-ext-diff --no-color HEAD
            for name in "${new_paths[@]}"; do
                rc=0
                git diff --binary --no-index --no-ext-diff --no-color /dev/null "$name" || rc=$?
                test "$rc" -eq 1 || exit 70
            done
        } | sha256sum | cut -d' ' -f1
    )
    test "$actual" = "$patch" || refuse 'Working diff not exact candidate'
}
resources() {
    other=$(ps -eo pid=,comm= | awk '$2 ~ /^(cargo|rustc|rustdoc)$/ {print $1,$2}')
    test -z "$other" || refuse "another Cargo or Rust compiler is present: $other"
    memory=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    [[ "$memory" =~ ^[0-9]+$ ]] && test "$memory" -ge 54525952 || refuse 'Less than 52 GiB MemAvailable'
}
test "$#" -eq 1 || refuse 'Use --preview or explicit --launch'
if test "$1" = --preview; then
    printf 'unit=%s base=%s tree=%s patch=%s profile=BrewFS-opt0 jobs=3 memory=38GiB CPU=500%% time=900s\n' \
        "$unit" "$base" "$tree" "$patch"
    exit 0
fi
test "$(id -un)" = dev && test "$(hostname)" = rbm-UrRupheQ || refuse 'Expected rbm dev'
job=$(dirname -- "$(realpath -e -- "$0")")
[[ "$job" =~ ^/home/dev/ax-brewfs-c2-namespace-20261112\.[A-Za-z0-9]+$ ]] &&
    test -O "$job" && test "$(stat -c %a "$job")" = 700 || refuse 'Expected independent private C2 root'
test -f "$job/candidate.patch" && test ! -L "$job/candidate.patch" || refuse 'Candidate patch missing'
printf '%s  %s\n' "$patch" "$job/candidate.patch" | sha256sum -c --status || refuse 'Candidate patch differs'
tool="$prior/toolchain/bin"
test "$("$tool/rustc" -V)" = 'rustc 1.99.1 (58cf8f9c3 2026-10-13)' || refuse 'rustc differs'
test "$("$tool/cargo" -V)" = 'cargo 1.99.1 (5f94df478 2026-08-27)' || refuse 'Cargo differs'

case "$1" in
--build)
    cg="/system.slice/$unit"
    grep -Fqx "0::$cg" /proc/self/cgroup || refuse 'Run only in the dedicated systemd cgroup'
    test "$(cat "/sys/fs/cgroup$cg/memory.max")" = 40802189312 &&
        test "$(cat "/sys/fs/cgroup$cg/memory.swap.max")" = 0 || refuse 'Memory cap is not effective'
    read -r quota period < "/sys/fs/cgroup$cg/cpu.max"
    [[ "$quota" =~ ^[0-9]+$ && "$period" =~ ^[0-9]+$ ]] &&
        test "$quota" -eq "$((5*period))" || refuse 'Five-CPU cap is not effective'
    validate_checkout
    resources
    export PATH="$tool:/usr/local/bin:/usr/bin:/bin" HOME="$job/home" CARGO_HOME="$job/cargo-home"
    export CARGO_TARGET_DIR="$job/target" RUSTC="$tool/rustc" CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0
    export CARGO_NET_OFFLINE=true
    (set -o noclobber; : > "$job/build.log") || refuse 'Build receipt already exists'
    exec >>"$job/build.log" 2>&1
    printf 'started=%s\nsource_commit=%s\nsource_tree=%s\ncandidate_patch_sha256=%s\n' "$(date -Is)" "$base" "$tree" "$patch"
    rustc -V; cargo -V
    cmd=(cargo --config profile.release.package.brewfs.opt-level=0 build --offline --locked --release --jobs 3
         --no-default-features --bin brewfs --features "$features")
    printf 'command='; printf '%q ' "${cmd[@]}"; printf '\n'
    finish() {
        status=$?
        trap - EXIT
        printf 'ended=%s\nexit_code=%s\n' "$(date -Is)" "$status" > "$job/build.exit.partial"
        mv -T --no-clobber -- "$job/build.exit.partial" "$job/build.exit"
        exit "$status"
    }
    trap finish EXIT
    "${cmd[@]}"
    candidate="$job/target/release/brewfs"
    test -x "$candidate" && test ! -L "$candidate" || refuse 'No candidate executable'
    grep -aFq BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES "$candidate" || refuse 'Payload retention marker absent'
    grep -aFq BREWFS_PACKED_WORKSPACE_READ_CONTROL "$candidate" || refuse 'Read namespace control marker absent'
    "$candidate" --version > "$job/brewfs-version.txt"
    artifact="$job/brewfs-c2-namespace-a34cb1ba-linux-x86_64"
    ! test -e "$artifact" && ! test -L "$artifact" || refuse 'Artifact destination already exists'
    mv -T --no-clobber -- "$candidate" "$artifact"
    test -x "$artifact" && test ! -e "$candidate" || refuse 'Artifact did not move'
    (set -o noclobber; sha256sum "$artifact" > "$job/artifact.sha256")
    ;;
--launch)
    exec 9>/home/dev/.ax-brewfs-c2-compiler.lock
    flock -n 9 || refuse 'C2 compiler setup already active'
    prior_state=$(systemctl show --property=ActiveState,SubState,Result,MainPID,ExecMainStatus,InvocationID "$prior_unit")
    for field in ActiveState=active SubState=exited Result=success MainPID=0 ExecMainStatus=0 "InvocationID=$prior_invocation"; do
        grep -Fqx -- "$field" <<< "$prior_state" || refuse "C4 prior unit differs: $field"
    done
    printf '%s  %s\n' "$prior_bin" "$prior/brewfs-resume-01-linux-x86_64" | sha256sum -c --status ||
        refuse 'C4 artifact SHA differs; no copy/build'
    state=$(systemctl show --property=LoadState --value "$unit" 2>/dev/null) ||
        test "$state" = not-found || refuse 'Cannot inspect unique C2 unit'
    test "$state" = not-found || refuse 'C2 unit name already exists'
    read -r branch_epoch branch_commit < "$job/branch-tip.txt"
    [[ "$branch_epoch" =~ ^[0-9]{10}$ ]] && test "$branch_commit" = "$base" || refuse 'Current upstream main receipt differs'
    age=$(( $(date +%s)-branch_epoch ))
    test "$age" -ge -10 && test "$age" -le 1800 || refuse 'Upstream main lookup is stale'
    resources
    available=$(df -PB1 -- "$job" | awk 'NR==2 {print $4}')
    [[ "$available" =~ ^[0-9]+$ ]] && test "$available" -ge 64424509440 ||
        refuse 'Need 60 GiB free for independent cache and build'
    printf 'source=%s unit=%s available_bytes=%s memory_kib=%s\n' "$base" "$unit" "$available" "$memory"
    mkdir -- "$job/home" "$job/cargo-home" "$job/target"
    mkdir -- "$job/target/release" "$job/cargo-home/registry"
    timeout --kill-after=10s 75s git clone -q --no-local --no-checkout -- "$prior/src-archive" "$job/source"
    git -C "$job/source" checkout -q --detach "$base"
    git -C "$job/source" apply --check "$job/candidate.patch"
    git -C "$job/source" apply "$job/candidate.patch"
    validate_checkout
    for p in deps build .fingerprint; do
        test -d "$prior/target/release/$p" && test ! -L "$prior/target/release/$p" || refuse 'C4 dependency directory missing'
    done
    timeout --kill-after=15s 240s cp -a --reflink=auto -- \
        "$prior/target/release/deps" "$prior/target/release/build" "$prior/target/release/.fingerprint" "$job/target/release/"
    for p in cache index src; do
        from="$prior/cargo-home/registry/$p/index.crates.io-1949cf8c6b5b557f"
        test -d "$from" && test ! -L "$from" || refuse 'Required offline registry directory missing'
        mkdir -- "$job/cargo-home/registry/$p"
        timeout --kill-after=10s 90s cp -a --reflink=auto -- "$from" "$job/cargo-home/registry/$p/"
    done
    test ! -e "$job/target/release/brewfs" || refuse 'Unexpected inherited executable'
    resources
    (set -o noclobber; printf 'owner_handoff_prior_unit=%s\nprior_invocation=%s\nsource=%s\npatch=%s\n' \
       "$prior_unit" "$prior_invocation" "$base" "$patch" > "$job/staged.txt")
    sudo -n /usr/bin/systemd-run --unit="$unit" --uid=dev --gid="$(id -gn)" --service-type=exec --remain-after-exit \
        --working-directory="$job/source" --property=MemoryMax=38G --property=MemorySwapMax=0 \
        --property=CPUQuota=500% --property=RuntimeMaxSec=900s --property=TimeoutStopSec=12s --property=Nice=8 \
        --property=PrivateNetwork=yes --property="ReadOnlyPaths=$prior" /bin/bash "$job/guard.sh" --build
    printf 'unit=%s root=%s\n' "$unit" "$job"
    ;;
*) refuse 'Unknown mode';;
esac
