"""Close and archive the target within the same guard as the original C4 v2 run.

The operator callback supplies a separately reviewed target request after the
runtime ready/process receipts exist. This module never reviews a request or
advances a suite ledger. The archive callback must read back remote artifacts.
"""
import json
from pathlib import Path
import re
import subprocess

from .experiment_io import linux_path
from .pilot_ledger_guard import multi_ledger_guard
from .runtime import handoff, transport
from .runtime.common import digest, load, pinned, private_dir, spec, write_new
from .suite_mount import cell_of

SCHEMA = "c4-handoff-guarded-run-v1"
CLOSE_REQUEST = "c4-handoff-target-closure-request-v1"
CLOSE_RESULT = "c4-handoff-target-closure-v1"
ARCHIVE_READBACK = "c4-handoff-archive-readback-v1"
REPO = Path(__file__).resolve().parents[1]
SCRIPTS = handoff.COLD_SCRIPTS
HELPER = SCRIPTS + "/close-c4-handoff-target.py"
PILOT_NODE = SCRIPTS + "/pilot-node.py"
EXTRA = ("c4_dispatch.py", "fio_workload.py", "suite_catalog.py", "suite_mount.py",
         "suite_plan.py", "suite_state.py", "suite_support.py")

# Unlike the node transport, this permits only the one known target helper.
# Do not import checkout code until reviewed file hashes have been verified.
BOOTSTRAP = """import hashlib,json,os,pathlib,sys
x=json.load(sys.stdin)
root=pathlib.Path(x['repo'])
if root.resolve(strict=True)!=root: raise ValueError('changed target checkout')
verified=set()
for pin in x['pins']:
 p=pathlib.Path(pin['path'])
 if not p.is_absolute() or p.resolve(strict=True)!=p or not p.is_file():
  raise ValueError('noncanonical target pin')
 with p.open('rb') as f:
  if hashlib.file_digest(f,'sha256').hexdigest()!=pin['sha256']: raise ValueError('target closure pin changed')
 verified.add(p)
if pathlib.Path(sys.executable).resolve() not in verified: raise ValueError('target Python not pinned')
required=set((root/'bench/runtime').glob('*.py'))
if not required.issubset(verified): raise ValueError('target runtime closure not pinned')
if x['cgroup']:
 p=pathlib.Path(x['cgroup'])
 if p.resolve(strict=True)!=p or not str(p).startswith('/sys/fs/cgroup/ax-'):
  raise ValueError('dedicated target cgroup required')
 (p/'cgroup.procs').write_text(str(os.getpid()))
helper=root/'experiments/unified-suite/pilot-aliyun/scripts/close-c4-handoff-target.py'
r=x['request']
if helper not in verified or pathlib.Path(r['path']) not in verified:
 raise ValueError('target helper and request must be verified')
os.chdir(root)
for key in list(os.environ):
 if key.startswith('PYTHON'): del os.environ[key]
os.environ.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPYCACHEPREFIX='/proc/ax-pilot-no-bytecode')
os.execv(sys.executable,[sys.executable,'-I','-B','-u','-X','pycache_prefix=/proc/ax-pilot-no-bytecode',str(helper),
 '--request',r['path'],'--sha256',r['sha256'],'--out',x['out']])
"""


def ref(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("regular original evidence file required")
    return {"path": str(path.resolve(strict=True)), "sha256": digest(path)}


def remote_pin(pin, path=None):
    if (not isinstance(pin, dict) or set(pin) != {"path", "sha256"}
            or not re.fullmatch(r"[a-f0-9]{64}", pin.get("sha256") or "")):
        raise ValueError("exact remote file and SHA-256 pin required")
    actual = linux_path(pin["path"])
    if str(actual) != pin["path"] or (path is not None and actual != path):
        raise ValueError("unexpected target receipt path")
    return pin


def closure_code(peer):
    """Expected remote byte pins generated for review from the pinned checkout."""
    repo = linux_path(peer["repo"])
    paths = [HELPER, PILOT_NODE, *(f"bench/{name}" for name in EXTRA + transport.SUPPORT_FILES)]
    paths += [p.relative_to(REPO).as_posix() for p in (REPO / "bench/runtime").glob("*.py")]
    return [{"path": str(repo / name), "sha256": digest(REPO / name)} for name in sorted(set(paths))]


def preflight(run):
    old = load(pinned(run["original_plan"]))
    plan = load(pinned(run["current_plan"]))
    cell = cell_of(plan, run["cell_id"])
    s = spec(run["handoff"], handoff.V2)
    cold = handoff.validate(s)
    if ((cell["suite"], cell["workload"], cell["arm"]["product"], cell["arm"].get("handoff_mode"))
            != ("C4", "handoff", s["filesystem"], s["mode"])):
        raise ValueError("v2 handoff does not match the reserved C4 cell")
    inventory = load(pinned(cold[0]["inventory"]))
    nodes = {row["id"]: row for row in inventory["nodes"]}
    cluster = next(p for p in plan["config"]["clusters"] if p["id"] == cell["pool"])
    for side in ("source", "target"):
        endpoint = s[side]
        peer = endpoint["peer"]
        n = nodes[endpoint["node_id"]]
        binding = spec(run["mount_readbacks"][side], "experiment-mount-v1")
        mount_pin = remote_pin(endpoint["mount_spec"])
        if (peer["transport"] != "ssh" or peer.get("alias") != n["ssh_alias"]
                or n["cluster_id"] != cell["pool"] or f"host:{n['host_id']}" not in cluster["resource_ids"]
                or mount_pin["sha256"] != run["mount_readbacks"][side]["sha256"]
                or binding["filesystem"] != s["filesystem"] or binding["anchor"] != endpoint["anchor"]
                or binding["cluster"] != cell["pool"] or binding["suite_binding"]["cell_id"] != cell["id"]
                or binding["suite_binding"]["plan"]["sha256"] != run["current_plan"]["sha256"]):
            raise ValueError("handoff endpoint does not bind the current physical slot, cell and mount plan")
    target = s["target"]
    if s["mode"] == "move" and nodes[s["source"]["node_id"]]["host_id"] == nodes[target["node_id"]]["host_id"]:
        raise ValueError("C4 move must use two physical hosts")
    route = run["target_close"]
    target_python(target["peer"])
    expected = closure_code(target["peer"])
    if route["code"] != expected:
        raise ValueError("target closure helper/runtime differs from reviewed code")
    mount_run = linux_path(target["mount_directory"])
    out = linux_path(route["output"])
    mount_point = linux_path(binding["mount_point"])
    if any(a.is_relative_to(b) or b.is_relative_to(a)
           for a, b in ((out, mount_run), (out, mount_point), (mount_run, mount_point))):
        raise ValueError("target close output, supervisor and mount must be disjoint")
    if not route["asset"]["path"].endswith("/asset.json"):
        raise ValueError("exact target asset.json pin required")
    remote_pin(route["asset"])
    return old, plan, cell, s, mount_point


def after_handoff(s, out, returned, mount_point):
    result_path = out / "result.json"
    result_pin = ref(result_path)
    if (load(out / "spec.json") != s or load(result_path) != returned
            or returned.get("schema") != "experiment-handoff-result-v2"
            or returned.get("passed") is not True or returned.get("target_mount_retained") is not True
            or returned["session_id"] != s["session_id"] or returned["mode"] != s["mode"]):
        raise ValueError("exact successful original v2 handoff receipt required")
    start = out / "target-start" / "protocol.jsonl"
    start_pin = ref(start)
    records = [json.loads(line) for line in start.read_text(encoding="utf-8").splitlines()]
    if len(records) != 1 or records[0].get("type") != "result":
        raise ValueError("one original target-start protocol result required")
    live = handoff.target_identity(records[0])
    cold_pin = returned["target_cold_check"]
    if cold_pin != ref(out / (handoff.COLD + ".json")):
        raise ValueError("target cold check was not pinned from this attempt")
    cold = load(pinned(cold_pin))
    if (cold.get("passed") is not True or cold.get("before_first_target_export") is not True
            or handoff.target_identity(cold["proof"]) != live
            or live["anchor"] != s["target"]["anchor"] or live["mount"]["point"] != str(mount_point)):
        raise ValueError("original target start and cold proof identify different mounts")
    return dict(result=result_pin, target_start=start_pin, target_cold=cold_pin, live=live)


def approved_close(run, s, original, authorization):
    """Accept separate operator-pinned review and exact original ready/process mirrors."""
    route = run["target_close"]
    target = s["target"]
    root = linux_path(target["mount_directory"])
    req = remote_pin(authorization["remote_request"])
    mirror = authorization["reviewed_request"]
    value = spec(mirror, CLOSE_REQUEST)
    pilot = next(pin for pin in route["code"] if pin["path"] == str(linux_path(target["peer"]["repo"]) / PILOT_NODE))
    if (mirror["sha256"] != req["sha256"] or value["cell_id"] != run["cell_id"]
            or value["mount_run"] != str(root) or value["mount_spec"] != target["mount_spec"]
            or value["asset"] != route["asset"] or value["pilot_node"] != pilot):
        raise ValueError("operator review does not authorize this target, asset and attempt")
    ready_pin = remote_pin(value["ready"], root / "ready.json")
    proc_pin = remote_pin(value["process"], root / "process.json")
    ready = load(pinned(authorization["ready_readback"]))
    process = load(pinned(authorization["process_readback"]))
    if (authorization["ready_readback"]["sha256"] != ready_pin["sha256"]
            or authorization["process_readback"]["sha256"] != proc_pin["sha256"]
            or ready["spec"] != target["mount_spec"] or process["spec"] != target["mount_spec"]
            or handoff.target_identity(ready) != original["live"]
            or any(process["process"][k] != v for k, v in original["live"]["process"].items())):
        raise ValueError("operator request does not bind the original target-start process and mount")
    return req, value


def target_python(peer):
    # Use the resolved original interpreter from the handoff node pins.
    root = linux_path(peer["repo"])
    candidates = [remote_pin(pin) for pin in peer["pins"] if not linux_path(pin["path"]).is_relative_to(root)
                  and re.fullmatch(r"python3(?:\.\d+)?", linux_path(pin["path"]).name)]
    if len(candidates) != 1:
        raise ValueError("single original target Python executable pin required")
    return candidates[0]


def remote_close(peer, route, request, approval, out):
    pins = [*route["code"], target_python(peer), request,
            *(approval[key] for key in ("ready", "process", "mount_spec", "asset"))]
    if len({pin["path"] for pin in pins}) != len(pins):
        raise ValueError("overlapping closure input and code pins")
    cmd = transport.ssh_command(peer, [peer["python"], "-I", "-B", "-u", "-c", BOOTSTRAP])
    envelope = dict(repo=peer["repo"], cgroup=peer.get("cgroup"), pins=pins, request=request, out=route["output"])
    with (out / "stdout.json").open("xb") as stdout, (out / "ssh.stderr.log").open("xb") as stderr:
        subprocess.run(cmd, input=json.dumps(envelope).encode(), stdout=stdout, stderr=stderr, check=True)
    return load(out / "stdout.json")


def checked_close(value, run, approved, request, original):
    if (value.get("schema") != CLOSE_RESULT or value.get("passed") is not True
            or value["cell_id"] != run["cell_id"] or value["request"] != request
            or any(value[k] != approved[k] for k in ("ready", "process", "mount_spec"))
            or value["original_mount"] != original["live"]["mount"] or value["anchor"] != original["live"]["anchor"]
            or any(value[k] is not True for k in ("all_exited", "all_unmounted"))
            or any(type(value[k]) is not int or value[k] != 0
                   for k in ("pending_upload_bytes", "pending_upload_operations"))):
        raise ValueError("target closure result differs from original target or did not close cleanly")
    root = linux_path(approved["mount_run"])
    out = linux_path(run["target_close"]["output"])
    for k in ("stopping", "stopped"):
        remote_pin(value[k], root / (k + ".json"))
    remote_pin(value["closure"], out / "suite-closure.json")
    remote_pin(value["debt"]["batch"], out / "debt" / "result.json")
    if len(value["debt"]["samples"]) != 2:
        raise ValueError("two original target debt samples required")
    for i, row in enumerate(value["debt"]["samples"]):
        remote_pin(row["sample"], out / "debt" / f"sample-{i}.json")
        remote_pin(row["native"], out / "debt" / f"native-{i}.prom")


def run_guarded(request, output, *, ownership, authorize_close, archive):
    """One claimed cell: handoff, separately reviewed target closure, archive.

    authorize_close(context) supplies pinned local reviewed request/ready/process
    readbacks and the remote request pin. archive(context) must independently
    preserve and read back the raw artifacts, returning a pin to an
    ARCHIVE_READBACK receipt bound to this cell, handoff and target closure.
    Both callbacks run synchronously inside the same canonical physical guard.
    No state is advanced, even on a successful bridge run.
    """
    if ownership is None or not callable(authorize_close) or not callable(archive):
        raise ValueError("exact active cell ownership and explicit review/archive callbacks required")
    run = spec(request, SCHEMA)
    old, plan, cell, s, mount_point = preflight(run)
    with multi_ledger_guard(run["canonical_control"], run["current_control"], old, plan, cell["id"],
                            ownership=ownership):
        out = private_dir(output)
        stage = "handoff"
        write_new(out / "intent.json", dict(request=request, cell_id=cell["id"], automatic_retry=False))
        try:
            handoff_out = out / "handoff"
            original = after_handoff(s, handoff_out, handoff.run(run["handoff"], handoff_out), mount_point)
            stage = "operator-review"
            context = dict(cell_id=cell["id"], original=original, target=s["target"], route=run["target_close"])
            approval = authorize_close(context)
            remote_request, approved = approved_close(run, s, original, approval)
            stage = "target-close"
            close_out = private_dir(out / "target-close")
            write_new(close_out / "intent.json", dict(authorization=approval, target_start=original["target_start"]))
            closed = remote_close(s["target"]["peer"], run["target_close"], remote_request, approved, close_out)
            checked_close(closed, run, approved, remote_request, original)
            close_pin = ref(close_out / "stdout.json")
            stage = "archive"
            archive_pin = archive(dict(**context, target_closure=closed, target_closure_readback=close_pin))
            readback = load(pinned(archive_pin))
            if (readback.get("schema") != ARCHIVE_READBACK or readback.get("passed") is not True
                    or readback.get("cell_id") != cell["id"] or readback.get("handoff") != original["result"]
                    or readback.get("target_closure_readback") != close_pin):
                raise ValueError("archive readback does not bind this handoff and target closure")
            final = dict(schema="c4-handoff-guarded-result-v1", passed=True, cell_id=cell["id"],
                         handoff=original["result"], target_closure_readback=close_pin, archive=archive_pin,
                         automatic_retry=False, suite_ledger_advanced=False)
            write_new(out / "result.json", final)
            return final
        except BaseException as error:
            write_new(out / "failed.json", dict(passed=False, stage=stage, error_type=type(error).__name__,
                      suite_ledger_advanced=False, automatic_retry=False,
                      scope="Inspect original local/remote receipts and retained claim; do not re-run this attempt."))
            raise
