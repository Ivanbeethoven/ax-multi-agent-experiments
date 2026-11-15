#!/usr/bin/env python3
"""Check a complete regeneration against the exact workload used in measurements."""
import hashlib
import json
from pathlib import Path
import shutil

base = Path("/data/cloud-smoke/2026-10-25")
used = (base / "generated/workload.jsonl").read_bytes()
regen = (base / "generated-verify/workload.jsonl").read_bytes()
used_header, used_body = used.split(b"\n", 1)
_, regen_body = regen.split(b"\n", 1)
stats = json.loads((base / "generated-verify/synthesis.json").read_text())
generated = sum(stats["generated_operation_counts"].values())
scaffold = sum(stats["scaffolding_operation_counts"].values())
source_units = sum(stats["selected_source_method_counts"].values())
checks = {"regenerated_operation_bytes_identical": used_body == regen_body,
          "regenerated_full_sha_with_same_initial_fingerprint": hashlib.sha256(used_header + b"\n" + regen_body).hexdigest() == hashlib.sha256(used).hexdigest(),
          "operation_accounting": generated == source_units + scaffold,
          "generated_operations": generated, "source_units": source_units, "scaffolding_operations": scaffold,
          "workload_sha256": hashlib.sha256(used).hexdigest(),
          "note": "Corrected an unlink-cycle scaffold counter in synthesis metadata only; actual JSONL operation bytes were unchanged. Original metadata retained separately."}
(base / "results/conversion-reproducibility.json").write_text(json.dumps(checks, indent=2) + "\n")
if not all(checks[k] for k in ("regenerated_operation_bytes_identical", "regenerated_full_sha_with_same_initial_fingerprint", "operation_accounting")):
    raise SystemExit(1)
shutil.copyfile(base / "generated/synthesis.json", base / "results/synthesis-before-bookkeeping-fix.json")
shutil.copyfile(base / "generated-verify/synthesis.json", base / "generated/synthesis.json")
print(json.dumps(checks, indent=2))
