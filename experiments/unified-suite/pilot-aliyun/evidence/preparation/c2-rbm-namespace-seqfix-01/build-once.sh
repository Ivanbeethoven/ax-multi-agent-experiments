#!/bin/bash
set -euo pipefail
umask 077
job=/home/dev/ax-brewfs-c2-namespace-20261112.F2B3t2Iv
c4=/home/dev/ax-brewfs-c4-build-20261112.GWrxnDJ8
base=ca07159f3da4b670e4aeb878faef928e69bc1634
tree=1ee7c97a427156d9c2cfe056f857e2e01efeb714
original=a34cb1ba0c94887368eaec85ae855d4766a39e29f38a023200525cf72fac2b40
follow=794c14b0248e9b90c93a799b753484209a36d0cb3cdad27864c0ec288c695935
previous_log=9839bfdce0d0875d7002ce169fb4199e9c3a2c2314e76e0190192c645acd718b
previous_artifact=e4c5b0b5ed88f4597f5e1d731dc4abeb305d26dc64cd4f8c489efb7b5e3fba88
old_unit=ax-brewfs-c2-hot-namespace-a34cb1ba-resume-01.service
old_invocation=202840f666e542d4926d57be165df07b
unit=ax-brewfs-c2-hot-namespace-seqfix-01.service
features=fuse-io-uring-runtime,workspace-overlay,native-packed-base,frozen-base-metadata,rustfs-ec-block-size-hint,observability
new=(src/workspace_overlay/meta_layer/packed_read_control.rs src/workspace_overlay/packed_v3/wire005/payload_retention.rs)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_OPTIONAL_LOCKS=0 GIT_ALLOW_PROTOCOL=file
fail(){ printf 'Refused: %s\n' "$*" >&2; exit 70; }
check_sha(){ printf '%s  %s\n' "$1" "$2" | sha256sum -c --status || fail "SHA differs: $2"; }
fingerprint(){
    {
        git diff --binary --no-ext-diff --no-color HEAD
        for f in "${new[@]}"; do
            rc=0
            git diff --binary --no-index --no-ext-diff --no-color /dev/null "$f" || rc=$?
            test "$rc" = 1 || exit 70
        done
    } | sha256sum | cut -d' ' -f1
}
check_source(){
    cd -- "$job/source"
    test "$(git rev-parse HEAD)" = "$base" && test "$(git rev-parse 'HEAD^{tree}')" = "$tree" || fail 'Source base differs'
    git diff --cached --quiet HEAD || fail 'Source index differs'
    test "$(git ls-files --others --exclude-standard)" = "$(printf '%s\n' "${new[@]}")" || fail 'Untracked source differs'
    git diff --check
    test "$(fingerprint)" = "$original" || fail 'Working tree differs from original candidate'
}
resources(){
    other=$(ps -eo pid=,comm= | awk '$2 ~ /^(cargo|rustc|rustdoc)$/ {print $1,$2}')
    test -z "$other" || fail 'A Rust compiler is already present'
    mem=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    [[ "$mem" =~ ^[0-9]+$ ]] && test "$mem" -ge 54525952 || fail 'Need 52 GiB available memory'
    free=$(df -PB1 -- "$job" | awk 'NR==2 {print $4}')
    [[ "$free" =~ ^[0-9]+$ ]] && test "$free" -ge 21474836480 || fail 'Need 20 GiB free disk'
}
test "$#" = 1 || fail 'Use --preview, --launch or service-only --build'
if test "$1" = --preview; then
    printf 'unit=%s base=%s original=%s followup=%s memory=38GiB CPU=500%% runtime=900s\n' "$unit" "$base" "$original" "$follow"
    exit 0
fi
test "$(id -un)" = dev && test "$(hostname)" = rbm-UrRupheQ || fail 'Expected rbm dev'
run=$(dirname -- "$(realpath -e -- "$0")")
[[ "$run" =~ ^"$job"/seqfix-01\.[A-Za-z0-9]+$ ]] || fail 'Unexpected run directory'
for p in "$job" "$run" "$job/source" "$job/home" "$job/cargo-home" "$job/target"; do
    test -d "$p" && test -O "$p" && test "$(realpath -e -- "$p")" = "$p" || fail "Unexpected directory: $p"
done
test "$(stat -c %a "$run")" = 700 || fail 'Run directory is not private'
for p in "$job/candidate.patch" "$run/followup.patch" "$run/branch-tip.txt"; do
    test -f "$p" && test -O "$p" && test ! -L "$p" || fail "Unexpected file: $p"
done
check_sha "$original" "$job/candidate.patch"
check_sha "$follow" "$run/followup.patch"
check_sha "$previous_log" "$job/build-resume-01.log"
check_sha "$previous_artifact" "$job/brewfs-c2-namespace-a34cb1ba-resume-01-linux-x86_64"
tool="$c4/toolchain/bin"
test "$("$tool/rustc" -V)" = 'rustc 1.99.1 (58cf8f9c3 2026-10-13)' || fail 'rustc differs'
test "$("$tool/cargo" -V)" = 'cargo 1.99.1 (5f94df478 2026-08-27)' || fail 'Cargo differs'
case "$1" in
--launch)
    exec 9>/home/dev/.ax-brewfs-c2-compiler.lock
    flock -n 9 || fail 'C2 compiler setup active'
    state=$(systemctl show "$old_unit" -p MainPID -p SubState -p Result -p ExecMainStatus -p InvocationID)
    for line in MainPID=0 SubState=exited Result=success ExecMainStatus=0 "InvocationID=$old_invocation"; do
        grep -Fqx -- "$line" <<< "$state" || fail "Original build terminal proof differs: $line"
    done
    read -r epoch tip extra < "$run/branch-tip.txt"
    [[ "$epoch" =~ ^[0-9]{10}$ ]] && test "$tip" = "$base" && test -z "$extra" || fail 'Upstream branch receipt differs'
    age=$(( $(date +%s)-epoch ))
    test "$age" -ge -10 && test "$age" -le 1800 || fail 'Need fresh upstream tip readback'
    state=$(systemctl show "$unit" -p LoadState --value 2>/dev/null) || test "$state" = not-found || fail 'Cannot inspect unit'
    test "$state" = not-found || fail 'Follow-up already launched'
    for p in "$job/target/release/brewfs" "$run/build.log" "$run/source-restored.txt" "$run/artifact.sha256"; do
        ! test -e "$p" && ! test -L "$p" || fail "Unexpected output: $p"
    done
    check_source
    git apply --check "$run/followup.patch"
    resources
    sudo -n /usr/bin/systemd-run --unit="$unit" --uid=dev --gid="$(id -gn)" --service-type=exec --remain-after-exit \
        --working-directory="$job/source" --property=MemoryMax=38G --property=MemorySwapMax=0 \
        --property=CPUQuota=500% --property=RuntimeMaxSec=900s --property=TimeoutStopSec=12s --property=Nice=8 \
        --property=PrivateNetwork=yes --property="ReadOnlyPaths=$c4" /bin/bash "$run/build-once.sh" --build
    printf 'unit=%s run=%s mem_available_kib=%s\n' "$unit" "$run" "$mem"
    ;;
--build)
    cg="/system.slice/$unit"
    grep -Fqx "0::$cg" /proc/self/cgroup || fail 'Missing dedicated cgroup'
    test "$(cat "/sys/fs/cgroup$cg/memory.max")" = 40802189312 &&
        test "$(cat "/sys/fs/cgroup$cg/memory.swap.max")" = 0 || fail 'Memory limit not enforced'
    read -r quota period < "/sys/fs/cgroup$cg/cpu.max"
    [[ "$quota" =~ ^[0-9]+$ && "$period" =~ ^[0-9]+$ ]] && test "$quota" = "$((5*period))" || fail 'CPU limit not enforced'
    check_source
    resources
    (set -o noclobber; : > "$run/build.log")
    exec >>"$run/build.log" 2>&1
    printf 'started=%s\nsource_commit=%s\nsource_tree=%s\noriginal_candidate_sha256=%s\nfollowup_patch_sha256=%s\n' \
        "$(date -Is)" "$base" "$tree" "$original" "$follow"
    original_file=$(sha256sum -- "${new[0]}" | cut -d' ' -f1)
    applied=0
    restore(){
        rc=$?
        trap - EXIT TERM INT
        if test "$applied" = 1; then
            if ! git apply --reverse --check "$run/followup.patch" || ! git apply --reverse "$run/followup.patch"; then
                printf 'followup_reverted=false\n' >&2
                exit 74
            fi
        fi
        check_source
        check_sha "$original_file" "${new[0]}"
        (set -o noclobber; printf 'restored=true\nrestored_source_candidate_sha256=%s\nrestored_file_sha256=%s\nended=%s\nbuild_exit_code=%s\n' \
            "$original" "$original_file" "$(date -Is)" "$rc" > "$run/source-restored.txt")
        exit "$rc"
    }
    trap restore EXIT
    trap 'exit 143' TERM
    trap 'exit 130' INT
    git apply "$run/followup.patch"
    applied=1
    printf 'compiled_source_candidate_sha256=%s\n' "$(fingerprint)"
    sha256sum -- "${new[0]}"
    export PATH="$tool:/usr/local/bin:/usr/bin:/bin" HOME="$job/home" CARGO_HOME="$job/cargo-home"
    export CARGO_TARGET_DIR="$job/target" RUSTC="$tool/rustc" CARGO_INCREMENTAL=0 CARGO_PROFILE_RELEASE_DEBUG=0 CARGO_NET_OFFLINE=true
    rustc -V; cargo -V
    cmd=(cargo --config profile.release.package.brewfs.opt-level=0 build --offline --locked --release --jobs 3
         --no-default-features --bin brewfs --features "$features")
    printf 'command='; printf ' %q' "${cmd[@]}"; printf '\n'
    "${cmd[@]}"
    candidate="$job/target/release/brewfs"
    artifact="$run/brewfs-c2-namespace-seqfix-01-linux-x86_64"
    test -x "$candidate" && test ! -L "$candidate" || fail 'No built executable'
    grep -aFq BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES "$candidate" || fail 'Payload retention marker absent'
    grep -aFq BREWFS_PACKED_WORKSPACE_READ_CONTROL "$candidate" || fail 'Read namespace control marker absent'
    "$candidate" --version > "$run/version.txt"
    mv -T --no-clobber -- "$candidate" "$artifact"
    test -x "$artifact" && test ! -e "$candidate" || fail 'Artifact did not move'
    (set -o noclobber; sha256sum "$artifact" > "$run/artifact.sha256")
    ;;
*) fail 'Invalid mode';;
esac
