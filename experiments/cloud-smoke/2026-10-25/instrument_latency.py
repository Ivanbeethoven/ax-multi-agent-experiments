#!/usr/bin/env python3
"""Add sample capture ONLY to an isolated copy for a separate timing=op run.

The primary timing=off binary is the unmodified repository tool. No latency is
claimed for that primary run. This script must target a copied replay.rs.
"""
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
edits = [
    ("    pub max_operation_time_ns: Option<u64>,", "    pub max_operation_time_ns: Option<u64>,\n    #[serde(skip_serializing_if = \"Option::is_none\")]\n    pub diagnostic_latency_samples_ns: Option<Vec<u64>>, "),
    ("    fn merge(&mut self, rhs: &Self) {", "    fn merge(&mut self, rhs: &Self) {\n        if let Some(samples) = &rhs.diagnostic_latency_samples_ns {\n            self.diagnostic_latency_samples_ns.get_or_insert_with(Vec::new).extend_from_slice(samples);\n        }"),
    ("            c.max_operation_time_ns = Some(0);", "            c.max_operation_time_ns = Some(0);\n            c.diagnostic_latency_samples_ns = Some(Vec::new());"),
    ("                    *count.operation_time_ns.as_mut().unwrap() += ns;", "                    count.diagnostic_latency_samples_ns.as_mut().unwrap().push(ns);\n                    *count.operation_time_ns.as_mut().unwrap() += ns;")]
for old, new in edits:
    if text.count(old) != 1:
        raise SystemExit(f"Expected one matching edit in isolated copy: {old}")
    text = text.replace(old, new)
p.write_text(text)
print(f"Instrumented isolated diagnostic source: {p}")
