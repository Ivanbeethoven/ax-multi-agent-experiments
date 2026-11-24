"""Resolve configuration and runtime paths locally or on the remote coordinator."""

import json
from pathlib import Path, PurePosixPath

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]


def find_baseline_configs_dir() -> Path:
    candidates = [
        PACKAGE_ROOT / "configs",
        Path("/srv/ax-cluster/baselines/configs"),
    ]
    for candidate in candidates:
        if (candidate / "baseline-runtime.json").is_file():
            return candidate
    raise FileNotFoundError("Cannot locate baseline-runtime.json")


def find_repo_configs_dir() -> Path:
    candidates = [
        REPO_ROOT / "configs",
        Path("/srv/ax-cluster/baselines/repo-configs"),
        Path("/srv/ax-cluster/direct/configs"),
    ]
    for candidate in candidates:
        if (candidate / "cluster.json").is_file():
            return candidate
    raise FileNotFoundError("Cannot locate cluster.json")


def load_baseline_runtime(path: Path | None = None) -> dict:
    cfg_path = path or (find_baseline_configs_dir() / "baseline-runtime.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def load_baseline_suite(path: Path | None = None) -> dict:
    cfg_path = path or (find_baseline_configs_dir() / "baseline-suite.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def load_baseline_mount_profiles(path: Path | None = None) -> dict:
    cfg_path = path or (find_baseline_configs_dir() / "baseline-mount-profiles.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def load_cluster_config(path: Path | None = None) -> dict:
    cfg_path = path or (find_repo_configs_dir() / "cluster.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def load_rustfs_backends(path: Path | None = None) -> dict:
    cfg_path = path or (find_repo_configs_dir() / "rustfs-backends.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def load_trace_inputs(path: Path | None = None) -> dict:
    cfg_path = path or (find_repo_configs_dir() / "trace-inputs.json")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def remote_run_root(runtime_cfg: dict | None = None) -> PurePosixPath:
    rt = runtime_cfg or load_baseline_runtime()
    return PurePosixPath(rt["remote_runs_root"]) / rt["run_id"]
