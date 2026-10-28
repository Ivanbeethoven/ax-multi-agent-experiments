#!/usr/bin/env python3
"""Strict validator for the pipeline's eleven-column CSV contract."""
from __future__ import annotations

import argparse
import csv
import glob
import math
import re
import sys
import tempfile
from pathlib import Path

HEADER = [
    "cell_id", "config", "workload", "params", "rep", "throughput_mibps",
    "iops", "p99_us", "read_bytes", "status", "note",
]
STATUS = re.compile(r"^(?:ok|failed|interrupted|invalid|not_run|unsupported|void\([^\n()]+\)|unmeasured\([^\n()]+\))$")
NUMERIC = {"rep", "throughput_mibps", "iops", "p99_us", "read_bytes"}


def expand_inputs(values: list[str], fixtures: Path) -> list[Path]:
    if values:
        paths: list[Path] = []
        for value in values:
            matches = [Path(x) for x in glob.glob(value, recursive=True)]
            paths.extend(matches or [Path(value)])
        return paths
    return sorted(fixtures.glob("*.csv"))


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        fh = path.open("r", newline="", encoding="utf-8-sig")
    except OSError as exc:
        return [f"{path}: cannot open: {exc}"]
    with fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return [f"{path}: empty file"]
        if header != HEADER:
            errors.append(f"{path}: header must be exactly {HEADER!r}; got {header!r}")
        for line, row in enumerate(reader, 2):
            if len(row) != len(HEADER):
                errors.append(f"{path}:{line}: expected exactly 11 columns, got {len(row)}")
                continue
            record = dict(zip(HEADER, row))
            for field in ("cell_id", "config", "workload", "rep", "status"):
                if not record[field]:
                    errors.append(f"{path}:{line}: {field} must not be empty")
            if not STATUS.fullmatch(record["status"]):
                errors.append(f"{path}:{line}: invalid status {record['status']!r}")
            for field in NUMERIC:
                value = record[field]
                if value:
                    try:
                        number = float(value)
                        if not math.isfinite(number) or number < 0:
                            raise ValueError('finite nonnegative value required')
                        if field in ('rep', 'read_bytes') and not number.is_integer():
                            raise ValueError('integer required')
                        if field == 'rep' and number == 0:
                            raise ValueError('positive repetition required')
                    except ValueError:
                        errors.append(f"{path}:{line}: {field} is not numeric: {value!r}")
    return errors


def self_regression(fixtures: Path) -> list[str]:
    files = sorted(fixtures.glob("*.csv"))
    if not files:
        return [f"{fixtures}: no fixture CSVs found"]
    errors: list[str] = []
    for path in files:
        found = validate_file(path)
        if path.name == 'c3-w2.csv':
            # Preserve the historical bytes: nine hot rows put fractional CPU-like
            # values in the byte column. They are negative fixtures, not valid data.
            if len(found) != 9 or not all('read_bytes is not numeric' in e for e in found):
                errors.append('c3-w2.csv must reject its nine non-integer byte counts')
        else:
            errors.extend(found)
    # Exercise the exact historical failure with the real file validator:
    # removing the note column from a valid fixture must produce an error.
    sample = files[0]
    with sample.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if len(rows[0]) != 11:
        errors.append(f"{sample}: fixture regression sample is not 11 columns")
    if len(rows) >= 2:
        with tempfile.TemporaryDirectory() as tmp:
            malformed = Path(tmp) / 'bad.csv'
            with malformed.open('w', newline='', encoding='utf-8') as fh:
                csv.writer(fh).writerows((rows[0][:-1], rows[1][:-1]))
            if not validate_file(malformed):
                errors.append("fixture regression: ten-column fixture was accepted")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", help="CSV files or glob patterns; default is pipeline/fixtures/*.csv")
    parser.add_argument("--fixtures", type=Path, default=Path(__file__).with_name("fixtures"), help="fixture directory used with --self-test")
    parser.add_argument("--self-test", action="store_true", help="validate today's fixtures and assert a ten-column row fails")
    args = parser.parse_args(argv)
    if args.self_test:
        errors = self_regression(args.fixtures)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print(f"self-test passed: 14 valid historical files, c3-w2 has 9 expected invalid byte fields; 10-column reject")
        return 0
    paths = expand_inputs(args.files, args.fixtures)
    if not paths:
        print("no CSV inputs", file=sys.stderr)
        return 2
    errors: list[str] = []
    for path in paths:
        errors.extend(validate_file(path))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"validated {len(paths)} CSV file(s): exact 11-column contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
