#!/usr/bin/env bash
set -euo pipefail
root=/mnt/c/Users/HXY/.cache/brewfs-hot-lower-20261112
exec >"$root/logs/merge-ax-final-check.log" 2>&1
trap 'printf "%s\n" "$?" >"$root/logs/merge-ax-final-check.exit"' EXIT
cd "$root/experiments"
export GIT_DIR=/mnt/c/Users/HXY/Documents/Codex/2026-10-22/https-www-usenix-org-conferences-author/work/ax-multi-agent-experiments/.git/worktrees/experiments
export GIT_WORK_TREE="$root/experiments" GIT_OPTIONAL_LOCKS=0
python3 scripts/check_repository.py
python3 scripts/check_repository_archive.py
python3 -m py_compile experiments/hot-lower-20261112/run_local.py experiments/local-metadata-profile-20261113/run_fuse_pairs.py
cd experiments/hot-lower-20261112
/home/hxy/.cache/brewfs-entity-validation-venv/bin/python -m unittest test_meter
/home/hxy/.cache/brewfs-entity-validation-venv/bin/python run_local.py --help
cd ../local-metadata-profile-20261113
python3 run_fuse_pairs.py --help
