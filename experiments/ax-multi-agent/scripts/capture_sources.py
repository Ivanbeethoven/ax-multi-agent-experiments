"""Record local code evidence only; never deploy or execute an experiment."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parents[2]
REPOS = {
    "brewfs": (WORK / "brewfs-upstream", "f80e7148c82e9f1ef235ab19318b89cb41a093a2"),
    "ax": (WORK / "ax-integration-upstream", "4ef115f88f3e0eedd2c2d9653fe04a25c1e158c6"),
}
SOURCES = [
    ("AX-CLI", "ax", "cmd/ax/main.go", "usage: ax workspace fork", "Client syntax; not proof of end-to-end fork."),
    ("AX-FORK", "ax", "internal/server/fork.go", '"kind":       "BrewFSWorkspaceFork"', "Requires operator CRD and Ready workspaceId/storageClassName."),
    ("AX-FORK-SERVER", "ax", "internal/server/server.go", "func (s *Server) ForkWorkspace", "Copies parent spec and records returned child storage class."),
    ("AX-VOLUME", "ax", "internal/substrate/client.go", "workspaceVolume.ExternalVolumeTemplate", "External volume selection and VOLUMES snapshot fidelity; no implied CSI checkpoint."),
    ("AX-RUNNER", "ax", "runner/runner.go", "if premountedWorkspaceEnabled()", "Skips setup; retains runner after child exit; stop terminates process group."),
    ("CSI-CONTRACT", "brewfs", "operator/brewfs-csi/README.md", "A writable workspace therefore", "Single writer; historical workspace-v1 canary and unverified independent remount."),
    ("CSI-RPC", "brewfs", "operator/brewfs-csi/src/main.rs", "Request<proto::CreateVolumeRequest>", "Deterministic existing volume identity; not workspace clone; snapshot RPC unimplemented."),
    ("ENTITY-ENTRY", "brewfs", "src/cli/entity_mount.rs", "fresh-only", "Fresh-only entry; S3 config and lease renewal handling."),
    ("ENTITY-DIR", "brewfs", "src/workspace_overlay/entity/resolver.rs", "MAX_DIRECTORY_ENTRIES: usize = 4096", "Entity directory enumeration bound."),
    ("ENTITY-PACKED", "brewfs", "src/workspace_overlay/entity/packed_lower.rs", "MAX_DIRECTORY_ENTRIES", "Packed lower path and bounded enumeration; not independent resume qualification."),
    ("ENTITY-DATA", "brewfs", "src/workspace_overlay/entity/data.rs", "MAX_IO_BYTES", "Upper object path and bounded IO; whole-object integrity behavior requires measurement."),
    ("ENTITY-FUSE", "brewfs", "src/fuse/entity.rs", "direct_io", "Entity IO policy differs from buffered ordinary VFS."),
    ("S3-UPLOAD", "brewfs", "src/cadapter/s3.rs", "put_object_create_only_durable", "Strict upload path; hint propagation must be qualified."),
    ("AGENT-WRAPPER", "brewfs", "deploy/ax-k3s/agent-image/run-agent.sh", "echo done", "Existing wrapper masks exit and writes telemetry to workspace; session home is elsewhere."),
]


def main():
    repo_records = {}
    for name, (path, expected) in REPOS.items():
        head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        if head != expected:
            raise SystemExit(f"Reviewed {name} HEAD changed; review claims before refreshing provenance")
        dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True)
        repo_records[name] = {"reviewed_commit": head, "path_relative_to_plan": f"../../../{path.name}", "dirty_at_capture": bool(dirty)}
    records = []
    for source_id, repo, relpath, needle, claim in SOURCES:
        path = REPOS[repo][0] / relpath
        raw = path.read_bytes()
        lines = raw.decode("utf-8").splitlines()
        matches = [i for i, line in enumerate(lines, 1) if needle in line]
        if not matches:
            raise SystemExit(f"Evidence locator absent: {source_id}: {needle}")
        records.append({"source_id": source_id, "repository": repo, "path": relpath,
                        "sha256": hashlib.sha256(raw).hexdigest(), "locator_lines": matches,
                        "locator_text": needle, "evidence_state": "present_in_code_or_recorded_document",
                        "supported_reading": claim, "runtime_executed_this_turn": False})
    missing = subprocess.run(["rg", "-l", "BrewFSWorkspaceFork|brewfsworkspaceforks", str(REPOS["brewfs"][0])], capture_output=True, text=True)
    if missing.returncode != 1 or missing.stdout or missing.stderr:
        raise SystemExit("Fork CRD absence finding changed or search failed; review before refreshing")
    summary_path = ROOT.parent / "agent-fuse-observations/derived/summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    result = {
        "schema_version": 1, "review_date": "2026-10-23", "scope": "local source inspection; no cluster/model/performance runs",
        "repositories": repo_records, "sources": records,
        "negative_findings": [{"repository": "brewfs", "search": "BrewFSWorkspaceFork|brewfsworkspaceforks", "tool": "rg -l", "exit_code": 1, "meaning": "No matching CRD/controller identifier found in reviewed checkout; does not exclude internal fork mechanisms or later commits."}],
        "prior_observation": {"path_relative_to_plan": "../agent-fuse-observations/derived/summary.json", "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(), "total_events": summary["total_events"], "task_count": len(summary["tasks"]), "evidence_state": "previously_derived_observation_not_recollected_here"}
    }
    (ROOT / "sources.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(records)} source files and one bounded negative finding.")


if __name__ == "__main__":
    main()
