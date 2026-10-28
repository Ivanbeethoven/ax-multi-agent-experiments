#!/usr/bin/env bash
set -euo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VALIDATE="$DIR/validate.py"
SUMMARIZE="$DIR/summarize.py"
FIXTURES="$DIR/fixtures"
BAD="$FIXTURES/bad"
CEILINGS="$FIXTURES/ceilings.json"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/brewfs-pipeline-selftest.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

printf 'selftest: fixtures=%s\n' "$FIXTURES"
printf 'selftest: ceilings=%s\n' "$CEILINGS"
printf '%s\n' '== validate: 15 real CSV fixtures =='
python3 "$VALIDATE" "$FIXTURES"/*.csv

printf '%s\n' '== validate: malformed fixtures must fail =='
bad_failures=0
for path in "$BAD"/*.csv; do
  if python3 "$VALIDATE" "$path"; then
    printf 'ERROR: expected rejection but accepted %s\n' "$path" >&2
    bad_failures=$((bad_failures + 1))
  else
    printf 'expected nonzero: %s\n' "$path"
  fi
done
if (( bad_failures )); then
  printf 'selftest: %d malformed fixture(s) were accepted\n' "$bad_failures" >&2
  exit 1
fi

printf '%s\n' '== summarize: cross-configuration table =='
python3 "$SUMMARIZE" "$FIXTURES"/*.csv \
  --ceilings "$CEILINGS" \
  --output-md "$TMP/summary.md" \
  --output-json "$TMP/summary.json"
cat "$TMP/summary.md"

python3 - "$TMP/summary.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    summary = json.load(fh)
entries = summary.get("entries", [])
configs = sorted({entry["config"] for entry in entries})
labels = sorted({label for entry in entries for label in entry["annotations"]})
if len(configs) < 3:
    raise SystemExit(f"expected a cross-configuration table, got {configs!r}")
if "cache-hit" not in labels:
    raise SystemExit(f"expected cache-hit annotation, got {labels!r}")
print(f"summary entries: {len(entries)}")
print(f"summary configs: {', '.join(configs)}")
print(f"summary labels: {', '.join(labels)}")
PY

printf 'selftest: PASS\n'
