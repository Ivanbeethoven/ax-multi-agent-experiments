"""Check saved review records and their files; never approve an experiment."""
import hashlib
import json
from pathlib import Path


CHECKS = ("inputs_and_resources", "clocks_and_cache", "durability",
          "correctness_and_coverage", "mechanism_and_operation_mode")
ARTIFACTS = ("items", "events", "inputs", "resources", "cache", "durability", "correctness")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def planned_population(cell):
    p = cell.get("parameters", {})
    if "requests_per_s" in p:
        return p["requests_per_s"] * p["measurement_s"]
    if cell["experiment_id"] == "T5":
        return len(p["selected"]) if "selected" in p else p.get("workspaces", 1)
    return p.get("tasks", p.get("consumers", p.get("retained_versions", p.get("workspaces", 1))))


def verify_review(result_path, cell, result):
    """A reviewer must first inspect raw observations; booleans alone are insufficient.

    This verifies provenance and coverage, not the scientific truth of the review.
    The review is external to result.json to avoid a circular content hash.
    """
    result_path = Path(result_path).resolve()
    root = result_path.parent
    path = root / "performance-audit.json"
    if not path.is_file():
        return ["missing_performance_audit"]
    problems = []
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
        if review.get("schema_version") != 1 or review.get("status") != "accepted":
            problems.append("review_not_accepted")
        if not isinstance(review.get("reviewed_by"), str) or not review["reviewed_by"].strip():
            problems.append("reviewer_missing")
        if review.get("cell_sha256") != sha256(root / "cell.json"):
            problems.append("review_cell_hash_mismatch")
        if review.get("result_sha256") != sha256(result_path):
            problems.append("review_result_hash_mismatch")
        for key in CHECKS:
            if review.get("checks", {}).get(key) is not True:
                problems.append("review_check:" + key)
        files = {}
        for key in ARTIFACTS:
            ref = review.get("artifacts", {}).get(key, {})
            relative = ref.get("path")
            if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
                problems.append("artifact_path:" + key)
                continue
            file = (root / relative).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                problems.append("artifact_missing_or_outside:" + key)
                continue
            if ref.get("sha256") != sha256(file):
                problems.append("artifact_hash:" + key)
                continue
            files[key] = file
        if "items" in files:
            items = json.loads(files["items"].read_text(encoding="utf-8"))
            expected = planned_population(cell)
            if not isinstance(items, list) or len(items) != expected:
                problems.append("item_coverage_count")
            else:
                ids = [str(item.get("item_id")) for item in items]
                planned_ids = cell["parameters"].get("selected", list(range(expected)))
                if len(set(ids)) != expected or set(ids) != {str(i) for i in planned_ids}:
                    problems.append("item_identity_coverage")
                if any(item.get("status") not in {"ok", "error", "timeout", "not_started", "unsupported", "rejected"} for item in items):
                    problems.append("invalid_item_status")
                if sum(item.get("status") == "ok" for item in items) != result["measurements"]["successful"]:
                    problems.append("item_success_mismatch")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        problems.append("unreadable_or_invalid_review")
    return problems
