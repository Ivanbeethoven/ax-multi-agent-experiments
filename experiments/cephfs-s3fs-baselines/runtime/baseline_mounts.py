"""Render cgroup-isolated mount specifications and commands for CephFS and s3fs."""

from collections import defaultdict
import json
from pathlib import Path, PurePosixPath


def slice_to_cgroup_dir(slice_name: str, unit: str) -> str:
    stem = slice_name[:-6] if slice_name.endswith(".slice") else slice_name
    parts = [p for p in stem.split("-") if p]
    hierarchy = "/".join("-".join(parts[: i + 1]) + ".slice" for i in range(len(parts)))
    return f"/sys/fs/cgroup/{hierarchy}/{unit}"


def load_profiles(path: Path | None = None) -> dict:
    if path is None:
        path = Path(__file__).resolve().parents[1] / "configs" / "baseline-mount-profiles.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_mount_record(request: dict, profiles: dict) -> dict:
    engine = request["engine"]
    if engine not in profiles["engines"]:
        raise ValueError(f"Unsupported baseline engine: {engine}")
    host_ssh = request["host"]
    if host_ssh not in profiles["host_limits"]:
        raise ValueError(f"Unknown host SSH alias: {host_ssh}")

    slot = int(request["slot"])
    if slot < 1:
        raise ValueError(f"Mount slot must be >= 1, got {slot}")

    rules = profiles["rules"]
    host_info = profiles["host_limits"][host_ssh]
    host_name = host_info["name"]
    run_root = PurePosixPath(request["root"])
    mount_id = request["id"]
    unit = rules["unit_pattern"].format(engine=engine, host=host_name.lower(), slot=slot)
    mount_dir = str(run_root / "mnt" / host_name / f"slot-{slot:03d}-{mount_id}")
    cache_root = str(run_root / "cache" / host_name / f"slot-{slot:03d}-{mount_id}")
    log_path = str(run_root / "logs" / host_name / f"{mount_id}.log")
    asok_path = f"{profiles['engines']['cephfs']['admin_socket_dir']}/{mount_id}.asok" if engine == "cephfs" else None

    overrides = dict(request.get("overrides") or {})
    eng_cfg = profiles["engines"][engine]
    binary = request.get("binary") or eng_cfg["binary"]
    resource_name = request.get("resource_profile", "default")
    res_prof = {} if resource_name == "default" else profiles["resource_profiles"][resource_name]

    effective = {
        "memory_max_bytes": int(res_prof.get("memory_max_bytes", rules["workspace_memory_max_bytes"])),
        "memory_swap_max_bytes": int(res_prof.get("memory_swap_max_bytes", rules["workspace_memory_swap_max_bytes"])),
        "disk_budget_bytes": int(res_prof.get("disk_budget_bytes", rules["workspace_disk_budget_bytes"])),
    }
    if engine == "cephfs":
        default_oc = int(res_prof.get("cephfs_client_oc_size_bytes", eng_cfg["client_oc_size_bytes"]))
        oc_bytes = int(overrides.get("client_oc_size_bytes", default_oc))
        effective.update(
            {
                "client_oc_size_bytes": oc_bytes,
                "fuse_options": list(eng_cfg["fuse_options"]),
                "admin_socket": asok_path,
            }
        )
    elif engine == "s3fs":
        default_dirty_mib = int(res_prof.get("s3fs_max_dirty_data_mib", eng_cfg["max_dirty_data_mib"]))
        effective.update(
            {
                "multipart_size_mib": int(overrides.get("multipart_size_mib", eng_cfg["multipart_size_mib"])),
                "parallel_count": int(overrides.get("parallel_count", eng_cfg["parallel_count"])),
                "max_dirty_data_mib": int(overrides.get("max_dirty_data_mib", default_dirty_mib)),
                "ensure_diskfree_mib": int(overrides.get("ensure_diskfree_mib", eng_cfg["ensure_diskfree_mib"])),
                "max_stat_cache_size": int(overrides.get("max_stat_cache_size", eng_cfg["max_stat_cache_size"])),
                "stat_cache_expire_seconds": int(overrides.get("stat_cache_expire_seconds", eng_cfg["stat_cache_expire_seconds"])),
                "direct_io": overrides.get("direct_io", "off") == "on",
                "disk_cache": overrides.get("disk_cache", "on") != "off",
                "enable_unsigned_payload": bool(eng_cfg["enable_unsigned_payload"]),
                "use_path_request_style": bool(eng_cfg["use_path_request_style"]),
                "complement_stat": bool(eng_cfg["complement_stat"]),
                "listobjectsv2": bool(eng_cfg["listobjectsv2"]),
                "noatime": bool(eng_cfg["noatime"]),
            }
        )

    return {
        "id": mount_id,
        "resource_profile": resource_name,
        "phase": request.get("phase", "measure"),
        "engine": engine,
        "binary": binary,
        "host": host_ssh,
        "host_name": host_name,
        "slot": slot,
        "unit": unit,
        "slice": rules["client_slice"],
        "cgroup": slice_to_cgroup_dir(rules["client_slice"], unit),
        "mount_directory": mount_dir,
        "cache_root": cache_root,
        "log_path": log_path,
        "admin_socket": asok_path,
        "workspace": request["workspace"],
        "parent_workspace": request.get("parent_workspace", "none"),
        "purpose": request.get("purpose", "baseline"),
        "cache_state": request.get("cache_state", "cold"),
        "windows": list(request.get("windows") or [request.get("phase", "measure")]),
        "overrides": overrides,
        "effective": effective,
    }


def validate_mounts_document(doc: dict, profiles: dict) -> dict:
    mounts = doc.get("mounts") or []
    by_window_host: dict[tuple[str, str], list[dict]] = defaultdict(list)
    seen_ids: set[str] = set()

    for rec in mounts:
        mid = rec["id"]
        if mid in seen_ids:
            raise ValueError(f"Duplicate mount id: {mid}")
        seen_ids.add(mid)
        for win in rec.get("windows") or ["default"]:
            by_window_host[(win, rec["host"])].append(rec)

    for (win, host_ssh), active in by_window_host.items():
        lim = profiles["host_limits"][host_ssh]
        slots = [m["slot"] for m in active]
        if len(slots) != len(set(slots)):
            raise ValueError(f"Slot collision in window {win!r} on {host_ssh}: {slots}")
        if len(active) > int(lim["max_active_mounts"]):
            raise ValueError(
                f"Active mounts ({len(active)}) exceed max_active_mounts ({lim['max_active_mounts']}) "
                f"in window {win!r} on {host_ssh}"
            )
        mem_sum = sum(int(m["effective"]["memory_max_bytes"]) for m in active)
        disk_sum = sum(int(m["effective"]["disk_budget_bytes"]) for m in active)
        if mem_sum > int(lim["client_memory_cap_bytes"]):
            raise ValueError(f"Memory budget exceeded in window {win!r} on {host_ssh}: {mem_sum}")
        if disk_sum > int(lim["client_disk_cap_bytes"]):
            raise ValueError(f"Disk budget exceeded in window {win!r} on {host_ssh}: {disk_sum}")

    return {"valid": True, "mount_count": len(mounts), "window_host_groups": len(by_window_host)}


def build_mounts_document(requests: list[dict], profiles: dict) -> dict:
    records = [build_mount_record(r, profiles) for r in requests]
    doc = {"schema": "ax-baseline-mounts-v1", "mounts": records}
    validate_mounts_document(doc, profiles)
    return doc


def render_mount_invocation(record: dict, spec: dict, profiles: dict) -> dict:
    engine = record["engine"]
    eff = record["effective"]
    eng_cfg = profiles["engines"][engine]
    cache_root = PurePosixPath(record["cache_root"])

    systemd_prefix = [
        "systemd-run",
        f"--unit={record['unit']}",
        f"--slice={record['slice']}",
        "--service-type=simple",
        "--collect",
        f"--property=MemoryMax={eff['memory_max_bytes']}",
        f"--property=MemorySwapMax={eff['memory_swap_max_bytes']}",
        "--property=MemoryAccounting=yes",
        "--property=CPUAccounting=yes",
        "--property=IOAccounting=yes",
        "--property=TasksMax=16384",
        "--property=LimitNOFILE=1048576",
        "--property=OOMPolicy=kill",
        "--property=Delegate=yes",
    ]

    if engine == "cephfs":
        subdir = spec["subdir"]
        if not subdir.startswith("/"):
            subdir = "/" + subdir
        fuse_opts = ",".join(eff["fuse_options"])
        exec_argv = [
            record["binary"],
            "-f",
            "-c",
            spec.get("ceph_conf", eng_cfg["ceph_conf"]),
            "-k",
            spec.get("keyring", eng_cfg["keyring"]),
            "-n",
            spec.get("client_id", eng_cfg["client_id"]),
            f"--client_mountpoint={subdir}",
            f"--client_oc_size={eff['client_oc_size_bytes']}",
            f"--admin-socket={record['admin_socket']}",
            "-o",
            fuse_opts,
            record["mount_directory"],
        ]
        if spec.get("filesystem") or eng_cfg.get("filesystem"):
            fs_name = spec.get("filesystem") or eng_cfg["filesystem"]
            exec_argv.insert(-3, f"--client_mds_namespace={fs_name}")
        return {
            "engine": "cephfs",
            "systemd_argv": [*systemd_prefix, "--", *exec_argv],
            "exec_argv": exec_argv,
            "prepare_dirs": [
                record["mount_directory"],
                str(cache_root),
                str(PurePosixPath(record["log_path"]).parent),
                str(PurePosixPath(record["admin_socket"]).parent),
            ],
            "expected_fstype": "fuse.ceph-fuse",
        }

    if engine == "s3fs":
        bucket = spec["bucket"]
        prefix = (spec.get("prefix") or "").strip("/")
        bucket_arg = f"{bucket}:/{prefix}" if prefix else bucket
        endpoint = spec["s3_endpoint"]
        passwd_file = spec.get("passwd_file") or eng_cfg["passwd_file"]
        tmp_dir = str(cache_root / "tmp")
        cache_dir = str(cache_root / "cache")

        opts = [
            f"passwd_file={passwd_file}",
            f"url={endpoint}",
            f"endpoint={eng_cfg['region']}",
            "sigv4",
            f"tmpdir={tmp_dir}",
            f"ensure_diskfree={eff['ensure_diskfree_mib']}",
            f"parallel_count={eff['parallel_count']}",
            f"multipart_size={eff['multipart_size_mib']}",
            f"max_dirty_data={eff['max_dirty_data_mib']}",
            f"max_stat_cache_size={eff['max_stat_cache_size']}",
            f"stat_cache_expire={eff['stat_cache_expire_seconds']}",
        ]
        if eff["use_path_request_style"]:
            opts.append("use_path_request_style")
        if eff["enable_unsigned_payload"]:
            opts.append("enable_unsigned_payload")
        if eff["complement_stat"]:
            opts.append("complement_stat")
        if eff["listobjectsv2"]:
            opts.append("listobjectsv2")
        if eff["noatime"]:
            opts.append("noatime")
        if eff["disk_cache"]:
            opts.append(f"use_cache={cache_dir}")
        if eff["direct_io"]:
            opts.append("direct_io")

        exec_argv = [
            record["binary"],
            "-f",
            bucket_arg,
            record["mount_directory"],
            "-o",
            ",".join(opts),
        ]
        prep_dirs = [
            record["mount_directory"],
            tmp_dir,
            str(PurePosixPath(record["log_path"]).parent),
        ]
        if eff["disk_cache"]:
            prep_dirs.append(cache_dir)
        return {
            "engine": "s3fs",
            "systemd_argv": [*systemd_prefix, "--", *exec_argv],
            "exec_argv": exec_argv,
            "prepare_dirs": prep_dirs,
            "expected_fstype": "fuse.s3fs",
        }

    raise ValueError(f"Unsupported engine: {engine}")
