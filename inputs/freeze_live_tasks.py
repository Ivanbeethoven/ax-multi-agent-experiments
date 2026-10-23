#!/usr/bin/env python3
"""Export fixed task inputs and separate grading material; never export gold patches.

Requires pyarrow. This script reads data and writes files; it does not start
containers, agents, grading, network requests, or an experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA256 = "030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25"
DATASET_REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
HARNESS_REVISION = "02e7a74ffd0b707aab73d203fe87bdc7c76afc8e"
REPOS = ["django/django", "pytest-dev/pytest", "scikit-learn/scikit-learn", "sympy/sympy"]
PROMPT_TEMPLATE = """Fix the issue in this repository checkout.

Repository: {repo}
Instance: {instance_id}

Issue statement:
{problem_statement}

Instructions:
- Inspect the code and existing tests, then implement a focused fix.
- Run relevant tests available in the checkout and review your changes.
- Work in this task's checkout. Do not use external issue discussions or reference solutions.
- Do not commit or push. Leave the changes in the working tree.
- End with a brief description of the change, tests run, and any remaining limitation.
"""


def digest(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value.encode("utf-8"))


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def test_list(value: object) -> list[str]:
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("Expected a list of test identifiers")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    source_sha = digest(args.source.read_bytes())
    if source_sha != SOURCE_SHA256:
        raise SystemExit(f"Wrong source snapshot: {source_sha}; expected {SOURCE_SHA256}")
    import pyarrow.parquet as pq

    rows = pq.read_table(args.source, use_threads=False).to_pylist()
    if len(rows) != 500:
        raise SystemExit(f"Expected 500 source tasks, got {len(rows)}")
    selected = []
    for repo in REPOS:
        candidates = sorted((row for row in rows if row["repo"] == repo), key=lambda row: row["instance_id"])
        if len(candidates) < 4:
            raise SystemExit(f"Fewer than four source tasks for {repo}")
        selected.extend(candidates[:4])
    provenance = {
        "dataset": "SWE-bench/SWE-bench_Verified",
        "dataset_url": "https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified",
        "split": "test",
        "dataset_git_revision": DATASET_REVISION,
        "source_file": "verified.parquet",
        "source_bytes": args.source.stat().st_size,
        "source_sha256": source_sha,
        "source_row_count": len(rows),
        "local_source_windows": "//wsl.localhost/Ubuntu-24.04/home/hxy/research/agent-fuse-swebench/verified.parquet",
        "local_source_wsl": "/home/hxy/research/agent-fuse-swebench/verified.parquet",
        "local_harness_path_wsl": "/home/hxy/research/agent-fuse-swebench/SWE-bench",
        "local_harness_git_revision": HARNESS_REVISION,
        "exporter_sha256": digest(Path(__file__).read_bytes()),
    }
    tasks, grading = [], []
    for index, row in enumerate(selected, 1):
        instance_id = row["instance_id"]
        for field in ("problem_statement", "base_commit", "environment_setup_commit", "image", "patch", "test_patch", "eval_script"):
            if not row.get(field):
                raise SystemExit(f"Missing {field} for {instance_id}; do not silently replace the task")
        prompt = PROMPT_TEMPLATE.format(**row)
        task = {key: row[key] for key in ("instance_id", "repo", "base_commit", "environment_setup_commit", "difficulty", "problem_statement")}
        task.update({
            "task_index": index,
            "snapshot_policy": "Use the original base_commit for this task; never unify distinct task bases.",
            "image_tag_from_dataset": row["image"],
            "image_digest": None,
            "problem_statement_sha256": digest(row["problem_statement"]),
            "prompt": prompt,
            "prompt_sha256": digest(prompt),
            "FAIL_TO_PASS": test_list(row["FAIL_TO_PASS"]),
            "PASS_TO_PASS": test_list(row["PASS_TO_PASS"]),
            "gold_patch_sha256": digest(row["patch"]),
            "runtime_qualification": "not_run",
            "base_grade": None,
            "reference_patch_grade": None,
        })
        tasks.append(task)
        folder = args.output_dir / "grading" / instance_id
        write_text(folder / "test.patch", row["test_patch"])
        write_text(folder / "eval.sh", row["eval_script"])
        oracle = {
            "instance_id": instance_id,
            "base_commit": row["base_commit"],
            "eval_type": row["eval_type"],
            "log_parser": row["log_parser"],
            "FAIL_TO_PASS": task["FAIL_TO_PASS"],
            "PASS_TO_PASS": task["PASS_TO_PASS"],
            "test_patch_sha256": digest(row["test_patch"]),
            "eval_script_sha256": digest(row["eval_script"]),
            "gold_patch_sha256": task["gold_patch_sha256"],
            "harness_git_revision": HARNESS_REVISION,
            "qualification": "not_run",
            "execution_note": "The source eval script embeds the test patch. Do not apply the exported test.patch a second time. Grade only in an isolated copy; never expose grading material to an agent.",
        }
        write_json(folder / "oracle.json", oracle)
        grading.append({"instance_id": instance_id, "oracle": f"{instance_id}/oracle.json", "test_patch": f"{instance_id}/test.patch", "eval_script": f"{instance_id}/eval.sh"})
    write_json(args.output_dir / "live-tasks.json", {
        "schema_version": 1,
        "frozen_on": "2026-10-23",
        "status": "inputs_frozen_runtime_unqualified",
        "execution_enabled": False,
        "provenance": provenance,
        "selection": {
            "repositories_in_order": REPOS,
            "tasks_per_repository": 4,
            "rule": "For each listed repository, sort all source rows by instance_id using case-sensitive Unicode lexicographic order and take the first four; concatenate repositories in the listed order.",
            "uses_performance_or_agent_success": False,
            "replacement_policy": "No automatic replacement. Record any image or grading qualification failure and revise the frozen protocol before comparative measurements; poor agent success or slow storage never justify replacement.",
            "scope": "A fixed four-repository case study, not a representative estimate over all 500 Verified tasks.",
        },
        "agent_input_contract": {
            "allowed": ["the task's base checkout", "the task's prompt", "the task's pinned execution environment"],
            "not_mounted_or_injected": ["this complete manifest", "grading/", "the source parquet", "gold patches", "reference patch filenames", "FAIL_TO_PASS", "PASS_TO_PASS"],
            "note": "The manifest is controller input. Emit only the per-task prompt into the agent workspace; test lists remain evaluator/controller metadata.",
        },
        "prompt_template": PROMPT_TEMPLATE,
        "tasks": tasks,
    })
    write_json(args.output_dir / "grading" / "index.json", {
        "schema_version": 1,
        "status": "source_material_only_not_executed",
        "agent_access": "forbidden_by_experiment_protocol",
        "provenance": provenance,
        "gold_patches_exported": False,
        "tasks": grading,
    })
    print(json.dumps({"tasks": [task["instance_id"] for task in tasks], "output_dir": str(args.output_dir), "grading_executed": False}, indent=2))


if __name__ == "__main__":
    main()
