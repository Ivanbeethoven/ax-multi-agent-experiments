#!/bin/bash
# One future same-path C2 resume, only after the C4 owner releases rbm.
set -euo pipefail
umask 077
prior=/home/dev/ax-brewfs-c4-build-20261112.GWrxnDJ8
unit=ax-brewfs-c2-hot-namespace-a34cb1ba-resume-01.service
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
    (set -o noclobber; : > "$job/build-resume-01.log") || refuse 'Build receipt already exists'
    exec >>"$job/build-resume-01.log" 2>&1
    printf 'started=%s\nsource_commit=%s\nsource_tree=%s\ncandidate_patch_sha256=%s\n' "$(date -Is)" "$base" "$tree" "$patch"
    rustc -V; cargo -V
    cmd=(cargo --config profile.release.package.brewfs.opt-level=0 build --offline --locked --release --jobs 3
         --no-default-features --bin brewfs --features "$features")
    printf 'command='; printf '%q ' "${cmd[@]}"; printf '\n'
    finish() {
        status=$?
        trap - EXIT
        printf 'ended=%s\nexit_code=%s\n' "$(date -Is)" "$status" > "$job/build-resume-01.exit.partial"
        mv -T --no-clobber -- "$job/build-resume-01.exit.partial" "$job/build-resume-01.exit"
        exit "$status"
    }
    trap finish EXIT
    "${cmd[@]}"
    candidate="$job/target/release/brewfs"
    test -x "$candidate" && test ! -L "$candidate" || refuse 'No candidate executable'
    grep -aFq BREWFS_PACKED_V3_RETAINED_PAYLOAD_BYTES "$candidate" || refuse 'Payload retention marker absent'
    grep -aFq BREWFS_PACKED_WORKSPACE_READ_CONTROL "$candidate" || refuse 'Read namespace control marker absent'
    "$candidate" --version > "$job/brewfs-resume-01-version.txt"
    artifact="$job/brewfs-c2-namespace-a34cb1ba-resume-01-linux-x86_64"
    ! test -e "$artifact" && ! test -L "$artifact" || refuse 'Artifact destination already exists'
    mv -T --no-clobber -- "$candidate" "$artifact"
    test -x "$artifact" && test ! -e "$candidate" || refuse 'Artifact did not move'
    (set -o noclobber; sha256sum "$artifact" > "$job/resume-01-artifact.sha256")
    ;;
--launch)
    test "$job" = /home/dev/ax-brewfs-c2-namespace-20261112.F2B3t2Iv ||
        refuse 'Resume must keep the same original C2 root'
    exec 9>/home/dev/.ax-brewfs-c2-compiler.lock
    flock -n 9 || refuse 'C2 compiler setup already active'
    first=ax-brewfs-c2-hot-namespace-a34cb1ba-01.service
    actual_state=$(systemctl show --property=ActiveState,SubState,Result,MainPID,ExecMainStatus,InvocationID "$first")
    for field in ActiveState=failed SubState=failed Result=timeout MainPID=0 ExecMainStatus=15 \
                 InvocationID=ffeb7e065ad648c285991c5564c69e0d; do
        grep -Fqx -- "$field" <<< "$actual_state" || refuse "First C2 unit differs: $field"
    done
    prior_sha=a073e3c782725cc21441dec02948d7b2a452c51b43b6915348de5ddf242b35a4
    printf '%s  %s\n' "$prior_sha" "$job/build.log" | sha256sum -c --status ||
        refuse 'First C2 build log SHA differs'
    for p in "$job/source" "$job/cargo-home" "$job/home" "$job/target" "$job/target/release/deps"; do
        test -d "$p" && test -O "$p" && test "$(realpath -e -- "$p")" = "$p" ||
            refuse 'The original independent C2 checkout, Cargo home or target differs'
    done
    for p in build-resume-01.log build-resume-01.exit build-resume-01.exit.partial resume-01-artifact.sha256 \
             brewfs-c2-namespace-a34cb1ba-resume-01-linux-x86_64 target/release/brewfs; do
        ! test -e "$job/$p" && ! test -L "$job/$p" || refuse "Unexpected stale or already completed output: $p"
    done
    test -f "$job/resume-owner-handoff.txt" && test -O "$job/resume-owner-handoff.txt" &&
        test ! -L "$job/resume-owner-handoff.txt" || refuse 'C4 owner release record is absent'
    read -r owner c4_unit c4_invocation extra < "$job/resume-owner-handoff.txt"
    test "$owner" = c4_handoff_path && test -z "$extra" &&
        [[ "$c4_unit" =~ ^ax-brewfs-c4-[a-z0-9-]+\.service$ && "$c4_invocation" =~ ^[a-f0-9]{32}$ ]] ||
        refuse 'C4 owner handoff must name the exact last invocation'
    c4_state=$(systemctl show --property=SubState,MainPID,InvocationID "$c4_unit")
    grep -Fqx "InvocationID=$c4_invocation" <<< "$c4_state" &&
        grep -Fqx MainPID=0 <<< "$c4_state" && grep -Eq '^SubState=(exited|failed|dead)$' <<< "$c4_state" ||
        refuse 'C4 last compiler unit is not terminal'
    read -r branch_epoch branch_commit < "$job/resume-branch-tip.txt"
    [[ "$branch_epoch" =~ ^[0-9]{10}$ ]] && test "$branch_commit" = "$base" || refuse 'Current upstream main receipt differs'
    age=$(( $(date +%s)-branch_epoch ))
    test "$age" -ge -10 && test "$age" -le 1800 || refuse 'Fresh upstream main read is required'
    state=$(systemctl show --property=LoadState --value "$unit" 2>/dev/null) ||
        test "$state" = not-found || refuse 'Cannot inspect unique C2 resume unit'
    test "$state" = not-found || refuse 'C2 resume unit name already exists'
    validate_checkout
    resources
    available=$(df -PB1 -- "$job" | awk 'NR==2 {print $4}')
    [[ "$available" =~ ^[0-9]+$ ]] && test "$available" -ge 21474836480 ||
        refuse 'Need 20 GiB free for one same-path resume'
    printf 'previous_log_sha256=%s owner=%s source=%s target=%s unit=%s memory_kib=%s\n' \
        "$prior_sha" "$owner" "$base" "$job/target" "$unit" "$memory"
    sudo -n /usr/bin/systemd-run --unit="$unit" --uid=dev --gid="$(id -gn)" --service-type=exec --remain-after-exit \
        --working-directory="$job/source" --property=MemoryMax=38G --property=MemorySwapMax=0 \
        --property=CPUQuota=500% --property=RuntimeMaxSec=900s --property=TimeoutStopSec=12s --property=Nice=8 \
        --property=PrivateNetwork=yes --property="ReadOnlyPaths=$prior" /bin/bash "$job/resume-once.sh" --build
    printf 'unit=%s root=%s\n' "$unit" "$job"
    ;;
*) refuse 'Unknown mode';;
esac
