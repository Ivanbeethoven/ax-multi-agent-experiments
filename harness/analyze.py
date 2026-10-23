"""Summarize recorded outcomes without treating failures or missing counters as zero."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics
from .evidence_audit import planned_population, sha256, verify_review


def _campaign(path):
    path = Path(path)
    index = json.loads(path.read_text(encoding="utf-8-sig"))
    order = index.get("order", [])
    if not order or len(order) != len(set(order)) or index.get("cells") != len(order):
        raise ValueError("invalid campaign order/count")
    cells = {}
    for identity in order:
        if not isinstance(identity, str) or Path(identity).name != identity or "/" in identity or "\\" in identity:
            raise ValueError("unsafe campaign cell ID")
        file = path.parent / (identity + ".json")
        digest = sha256(file)
        if index.get("cell_sha256", {}).get(identity) != digest:
            raise ValueError("campaign cell hash missing or changed")
        cell = json.loads(file.read_text(encoding="utf-8-sig"))
        if cell["cell_id"] != identity:
            raise ValueError("campaign cell identity mismatch")
        cells[identity] = (cell, digest)
    return cells


def summarize(paths, campaign_file=None):
    planned = _campaign(campaign_file) if campaign_file is not None else None
    rows = []
    seen = set()
    for path in paths:
        path = Path(path)
        r = json.loads(path.read_text(encoding="utf-8-sig"))
        c = json.loads((path.parent / "cell.json").read_text(encoding="utf-8-sig"))
        digest = sha256(path.parent / "cell.json")
        for key in ("cell_id", "baseline_id"):
            if r.get(key) != c[key]:
                raise ValueError("result " + key + " mismatch")
        if r.get("cell_sha256") != digest:
            raise ValueError("result cell hash mismatch")
        if planned is not None and (c["cell_id"] not in planned or planned[c["cell_id"]][1] != digest):
            raise ValueError("result not in the frozen campaign")
        if c["cell_id"] in seen:
            raise ValueError("duplicate cell; choose the planned attempt rather than silently pooling retries")
        seen.add(c["cell_id"])
        m = r.get("measurements") or {}
        n, ok = m.get("requested"), m.get("successful")
        if n is None and ok is not None:
            raise ValueError("successful count without requested denominator")
        if n is not None and (type(n) is not int or n <= 0 or type(ok) is not int or not 0 <= ok <= n):
            raise ValueError("invalid outcome counts")
        if n is not None and n != planned_population(c):
            raise ValueError("requested denominator differs from planned population")
        elapsed = m.get("elapsed_s")
        if elapsed is not None and (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError("invalid elapsed time")
        reasons = verify_review(path, c, r)
        if planned is None:
            reasons.append("campaign_coverage_unknown")
        eligible = (not reasons and r.get("performance_claims_allowed") is True
                    and c["profile"] == "full" and r["scope"] == "real")
        rows.append({"cell_id": c["cell_id"], "pair_id": c["pair_id"], "experiment_id": c["experiment_id"],
            "condition": json.dumps({k: v for k, v in c.get("parameters", {}).items()
                                     if k not in {"seed", "input_pair_seed"}}, sort_keys=True),
            "baseline_id": c["baseline_id"], "variant": c["variant"], "status": r["status"], "scope": r["scope"],
            "profile": c["profile"], "requested": n, "successful": ok,
            "completion_rate": ok / n if n else None, "elapsed_s": elapsed,
            "eligible": eligible, "review_problems": reasons, "planned_requested": planned_population(c),
            "all_completed": n is not None and ok == n and r["status"] == "ok",
            "physical_bytes": m.get("physical_bytes"), "backend_requests": m.get("backend_requests")})
    if planned is not None:
        for identity, (c, _) in planned.items():
            if identity in seen:
                continue
            rows.append({"cell_id": identity, "pair_id": c["pair_id"], "experiment_id": c["experiment_id"],
                "condition": json.dumps({k: v for k, v in c.get("parameters", {}).items()
                                         if k not in {"seed", "input_pair_seed"}}, sort_keys=True),
                "baseline_id": c["baseline_id"], "variant": c["variant"], "status": "not_run",
                "scope": None, "profile": c["profile"], "requested": None, "successful": None,
                "completion_rate": None, "elapsed_s": None, "eligible": False,
                "review_problems": ["result_not_received"], "planned_requested": planned_population(c),
                "all_completed": False, "physical_bytes": None, "backend_requests": None})
    groups = []
    for key in sorted({(r["experiment_id"], r["baseline_id"], r["variant"], r["profile"], r["condition"]) for r in rows}):
        group = [r for r in rows if (r["experiment_id"], r["baseline_id"], r["variant"], r["profile"], r["condition"]) == key]
        groups.append({"experiment_id": key[0], "baseline_id": key[1], "variant": key[2], "profile": key[3],
            "condition": key[4],
            "received_cells": sum(r["status"] != "not_run" for r in group),
            "not_run_cells": sum(r["status"] == "not_run" for r in group),
            "listed_cells": len(group), "planned_requested": sum(r["planned_requested"] for r in group),
            "observed_successful": sum(r["successful"] or 0 for r in group),
            "cells_with_counts": sum(r["requested"] is not None for r in group),
            "failed_or_rejected_cells": sum(r["status"] not in {"ok", "not_run"} for r in group),
            "eligible_cells": sum(r["eligible"] for r in group),
            "all_completed_cells": sum(r["all_completed"] for r in group)})
    return {"rows": rows, "groups": groups, "coverage": {
        "planned_cells": len(planned) if planned is not None else None,
        "received_cells": len(seen), "not_run_cells": len(planned) - len(seen) if planned is not None else None},
        "note": "Without a frozen campaign and checked review files, no performance pairing is eligible. Unrun cells remain explicit; unknown outcomes stay null. Review-file checking does not replace inspection of experimental meaning."}


def paired_effects(rows, reference, treatment):
    """Labels are BASELINE:VARIANT. Pair means identical experimental block."""
    def label(r):
        return r["baseline_id"] + ":" + r["variant"]
    selected = [r for r in rows if label(r) in {reference, treatment}]
    pairs, missing, excluded = [], 0, 0
    for pair in sorted({r["pair_id"] for r in selected}):
        values = [r for r in selected if r["pair_id"] == pair]
        group = {label(r): r for r in values}
        if len(values) != len(group):
            raise ValueError("duplicate baseline/variant in a paired block")
        if reference not in group or treatment not in group:
            missing += 1
            continue
        a, b = group[reference], group[treatment]
        if a.get("condition", "{}") != b.get("condition", "{}"):
            raise ValueError("paired rows have different workload conditions")
        if not all(r["eligible"] and r["all_completed"] and r["elapsed_s"] is not None and r["elapsed_s"] > 0 for r in (a, b)):
            excluded += 1
            continue
        if a["experiment_id"] != b["experiment_id"] or a["profile"] != b["profile"]:
            raise ValueError("paired rows refer to different experiments/profiles")
        pairs.append({"pair_id": pair, "condition": a.get("condition", "{}"), "seconds_saved": a["elapsed_s"] - b["elapsed_s"],
                      "time_reduction_fraction": 1 - b["elapsed_s"] / a["elapsed_s"]})
    conditions = sorted({p["condition"] for p in pairs})
    return {"reference": reference, "treatment": treatment, "complete_successful_pairs": len(pairs),
        "missing_pairs": missing, "excluded_failure_or_unqualified_pairs": excluded, "pairs": pairs,
        "mean_time_reduction_fraction": statistics.mean(p["time_reduction_fraction"] for p in pairs) if len(conditions) == 1 else None,
        "by_condition": [{"condition": condition, "pairs": sum(p["condition"] == condition for p in pairs),
            "mean_time_reduction_fraction": statistics.mean(p["time_reduction_fraction"] for p in pairs if p["condition"] == condition)}
                         for condition in conditions],
        "interpretation": "Conditional time comparison for complete qualified pairs only. Always report failure/unrun coverage alongside it; this does not establish a causal mechanism."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, nargs="*", default=[])
    p.add_argument("--campaign", type=Path, help="frozen index.json; required for performance eligibility")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--reference")
    p.add_argument("--treatment")
    args = p.parse_args()
    if not args.results and not args.campaign:
        p.error("provide a campaign or result files")
    if bool(args.reference) != bool(args.treatment):
        p.error("reference and treatment must be supplied together")
    result = summarize(args.results, args.campaign)
    if args.reference:
        result["paired_effects"] = paired_effects(result["rows"], args.reference, args.treatment)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output / "cells.csv").open("w", encoding="utf-8", newline="") as out:
        if result["rows"]:
            w = csv.DictWriter(out, fieldnames=list(result["rows"][0]))
            w.writeheader()
            w.writerows(result["rows"])
    print(json.dumps({"status": "summarized", "received_cells": len(result["rows"])}))

if __name__ == "__main__":
    main()
