"""Dispatch read-only file loaders across the selected hosts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import cluster_lib as cl

def batch_c3_loader(tasks_by_host: dict[str, list[dict]], run_tag: str) -> list[dict]:
    hosts = sorted(tasks_by_host.keys())
    remote_paths: dict[str, str] = {}
    for hname in hosts:
        rp = f"{cl.RUN_ROOT}/hosts/{hname}/batch/{run_tag}-c3-tasks.json"
        lt = cl.RUN_ROOT / "mounts" / "tmp" / f"{run_tag}-{hname}-c3-tasks.json"
        lt.parent.mkdir(parents=True, exist_ok=True)
        lt.write_text(json.dumps(tasks_by_host[hname], indent=2) + "\n", encoding="utf-8")
        cl.sync_file_to_host(hname, lt, rp)
        lt.unlink(missing_ok=True)
        remote_paths[hname] = rp

    def _run_host(hname: str) -> list[dict]:
        rp = remote_paths[hname]
        p = cl.run_on_host(
            hname,
            ["/usr/bin/python3", "/srv/ax-cluster/direct/scripts/host_batch_runner.py", "c3-batch", "--tasks", rp],
            timeout=7200.0,
        )
        cl.run_on_host(hname, ["rm", "-f", rp], check=False, log_cmd=False)
        return json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])["results"]

    with ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        per_host = list(pool.map(_run_host, hosts))
    return [r for sub in per_host for r in sub]
