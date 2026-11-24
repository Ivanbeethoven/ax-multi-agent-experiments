"""Run a GraftFS merge with a durable operation ID and a JSON result."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def run_merge(binary, meta_url, namespace, child, target, operation_file):
    child, target = str(uuid.UUID(child)), str(uuid.UUID(target))
    identity = dict(child=child, target=target, meta_url=meta_url, namespace=namespace)
    path = Path(operation_file)
    if path.exists():
        pending = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(pending, dict) or pending.get("identity") != identity:
            raise ValueError("operation file belongs to a different merge")
        if not isinstance(pending.get("operation"), str):
            raise ValueError("operation file has no UUID")
        pending["operation"] = str(uuid.UUID(pending["operation"]))
    else:
        pending = dict(identity=identity, operation=str(uuid.uuid4()))
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(pending, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    command = [str(binary), "workspace", "--meta-backend", "redis", "--meta-url", meta_url,
               "--workspace-namespace", namespace, "merge", child, target,
               "--operation", str(uuid.UUID(pending["operation"]))]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"merge returned no JSON result (exit {completed.returncode}): "
                           f"{completed.stderr}") from error
    expected = dict(operation=pending["operation"], child=child, target=target)
    if not isinstance(result, dict) or result.get("request") != expected or not isinstance(result.get("committed"), bool):
        raise RuntimeError("merge response identity or status is invalid")
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    if completed.returncode != 0 and result["committed"]:
        raise RuntimeError("merge reported a commit with an unsuccessful exit status")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("binary", "meta-url", "namespace", "child", "target", "operation-file"):
        parser.add_argument("--" + option, required=True)
    args = parser.parse_args()
    try:
        result = run_merge(args.binary, args.meta_url, args.namespace, args.child,
                           args.target, args.operation_file)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(json.dumps(dict(error=str(error))))
        return 1
    print(json.dumps(result))
    return 0 if result["committed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
