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
printf '%s\n' '== validate: 14 historical CSVs plus expected invalid C3 W2 =='
python3 "$VALIDATE" --self-test
valid=()
for path in "$FIXTURES"/*.csv; do
  [[ $(basename "$path") == c3-w2.csv ]] || valid+=("$path")
done

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
python3 "$SUMMARIZE" "${valid[@]}" \
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
if "cache-hit" in labels or "kernel-cache" in labels:
    raise SystemExit(f"unsupported causal annotation: {labels!r}")
if not all('status_counts' in entry for entry in entries):
    raise SystemExit('missing outcome counts')
print(f"summary entries: {len(entries)}")
print(f"summary configs: {', '.join(configs)}")
print(f"summary labels: {', '.join(labels)}")
PY

printf 'selftest: PASS\n'
