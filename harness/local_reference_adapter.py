"""Local copy-only harness check. This is NOT BrewFS, XFS/reflink or a cloud baseline."""
import json
from pathlib import Path
import re
import shutil
import sys
import uuid
from harness.fixtures import checked_root, safe_path

OPS = ["discover", "create", "fork", "save", "open", "release"]

def main():
    request = json.load(sys.stdin)
    op, p = request["operation"], request["payload"]
    root = checked_root(Path(request["workspace_root"]))
    data = {}
    def path(value):
        candidate = checked_root(Path(value))
        candidate.relative_to(root)
        return candidate
    def copy(source, name):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", str(name)):
            raise ValueError("invalid workspace name")
        source = path(source)
        for entry in source.rglob("*"):
            safe_path(source, entry.relative_to(source).as_posix())
        dest = root / ("ws-" + str(name))
        shutil.copytree(source, dest)
        return {"workspace_id": name, "path": str(dest)}
    if op == "discover":
        data = {"operations": OPS, "runtime": {"versions": {"python": sys.version.split()[0]},
                "resources": {"local_only": True}, "configuration": {"copy_only": True}}}
    elif op == "create":
        data = copy(p["source_path"], p["workspace_id"])
    elif op == "fork":
        data = copy(p["parent_path"], p["workspace_id"])
    elif op == "save":
        version_id = "v-" + uuid.uuid4().hex
        dest = root / version_id
        source = path(p["path"])
        for entry in source.rglob("*"):
            safe_path(source, entry.relative_to(source).as_posix())
        shutil.copytree(source, dest)
        data = {"version_id": version_id, "remote_ack": False, "durability": "local_copy_only"}
    elif op == "open":
        if not re.fullmatch(r"v-[0-9a-f]{32}", p["version_id"]):
            raise ValueError("invalid version id")
        data = copy(root / p["version_id"], p["workspace_id"])
    elif op == "release":
        path(p["path"])
        data = {"released": True, "retained_files_for_inspection": True}
    else:
        print(json.dumps({"status": "unsupported", "scope": "local_reference", "data": None,
                          "evidence": "copy-only reference adapter", "telemetry": None}))
        return
    print(json.dumps({"status": "ok", "scope": "local_reference", "data": data,
                      "evidence": {"implementation": "local_copy_only", "paper_eligible": False}, "telemetry": None}))

if __name__ == "__main__":
    main()
