"""Replay a published pilot bundle without Docker or the original working directory."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile


def restore(archive, destination):
    def checked_mode(member, target):
        checked = tarfile.data_filter(member, target)
        if checked and (checked.isfile() or checked.isdir()):
            # Preserve fingerprinted rwx bits; never restore special mode bits.
            checked = checked.replace(mode=member.mode & 0o777)
        return checked
    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter=checked_mode)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--binary", type=Path, required=True)
    args = p.parse_args()
    bundle, out, binary = args.bundle.resolve(), args.output.resolve(), args.binary.resolve()
    hashes = bundle / "artifacts.sha256.json"
    if hashes.exists():
        for name, digest in json.loads(hashes.read_text()).items():
            path = (bundle / name).resolve()
            if not path.is_relative_to(bundle): raise ValueError("Unsafe manifest path")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest: raise ValueError("Bundle hash mismatch: " + name)
    out.mkdir(parents=True, exist_ok=False)
    restore(bundle / "initial-workspace.tar.gz", out / "input")
    root = out / "replay"
    subprocess.run([str(binary), "init", "--root", str(root)], check=True, capture_output=True)
    shutil.copytree(out / "input/workspace", root, dirs_exist_ok=True)
    subprocess.run([str(binary), "run", "--trace", str(bundle / "task-capture/trace.jsonl"),
                    "--root", str(root), "--output", str(out / "replay.json"),
                    "--workers", "1", "--pace", "asap", "--timing", "off"], check=True)
    actual = json.loads(subprocess.check_output([str(binary), "fingerprint", "--root", str(root)], text=True))["initial_tree_sha256"]
    expected = json.loads((bundle / "result.json").read_text())
    if actual != expected["captured_final_tree_sha256"]: raise ValueError("Final tree mismatch")
    report = json.loads((out / "replay.json").read_text())
    for key in ("actual_read_bytes", "actual_write_bytes"):
        if report["totals"][key] != expected["metrics"][key]: raise ValueError("Byte count mismatch")
    result = {"valid": True, "final_tree_sha256": actual, "mode_preserving_safe_extraction": True,
              "initial_tree_hash_verified": report["initial_tree_hash_verified"]}
    (out / "verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__": main()
