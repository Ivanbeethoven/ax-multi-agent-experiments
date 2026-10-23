"""Run four small, copy-only end-to-end checks in a fresh temporary directory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.generate_campaign import make_cells
from harness.run_cell import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="ax-harness-smoke-") as directory:
        tmp = Path(directory)
        config = json.loads((ROOT / "configs/adapters/local-reference.json").read_text(encoding="utf-8"))
        config["command_cwd"] = str(ROOT)
        for spec in config["operations"].values():
            if spec:
                spec["argv"][0] = sys.executable
        adapter_path = tmp / "adapter.json"
        adapter_path.write_text(json.dumps(config), encoding="utf-8")
        for i, cell in enumerate(make_cells("smoke", repetitions=1)):
            cell_path = tmp / f"cell-{i}.json"
            cell_path.write_text(json.dumps(cell), encoding="utf-8")
            result = run(cell_path, adapter_path, tmp / f"run-{i}", execute=True)
            results.append({"experiment": cell["experiment_id"], "status": result["status"],
                            "measurements": result.get("measurements"), "error": result.get("error"),
                            "performance_claims_allowed": result["performance_claims_allowed"]})
            if result["status"] != "ok":
                for log in (tmp / f"run-{i}" / "logs").glob("runner-error.log"):
                    print(log.read_text(encoding="utf-8"), file=sys.stderr)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
        "passed": all(r["status"] == "ok" and not r["performance_claims_allowed"] for r in results),
        "scope": "copy-only temporary smoke checks; no real baseline, cloud, model, distributed or performance run",
        "results": results,
        "source_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted((ROOT / "harness").glob("*.py"))}}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": len(results), "report": str(args.report)}))
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
