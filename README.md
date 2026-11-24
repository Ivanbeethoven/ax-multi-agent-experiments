# GraftFS Evaluation & Cluster Benchmark Artifact (OSDI '27 Anonymized)

This anonymized branch provides the cluster benchmark harness, FUSE trace
replayer (`tools/full-fuse-replay`), mount/storage orchestration scripts
(`bench/cluster`, `infra/cluster`, `infra/rustfs`, `infra/cephfs-s3fs`,
`experiments/cephfs-s3fs-baselines`), configuration templates (`configs/`),
and unit test suite (`tests/`) for **GraftFS: Delta-Based Workspaces for Cloud
Multi-Agent Workloads with Packed Metadata and Variable-Granularity Object
Storage**.

## Structure

- `bench/cluster/`: Multi-host benchmark suite (FIO, Fork/Depth, Packed Metadata, and 64-agent FUSE trace replay).
- `infra/cluster/`, `infra/rustfs/`, `infra/graftfs/`, `infra/cephfs-s3fs/`: Deployment and backend orchestration scripts.
- `tools/full-fuse-replay/`: Rust FUSE trace replayer with cross-host byte-for-byte output verification.
- `configs/`: Sanitized six-node cluster topology (`node-coord`, `node-meta`, `node-store-1..4`), mount profiles, and suite specifications.
- `tests/`: Automated unit test suite validating scheduling, mounting, backend hints, and result aggregation.

## Running Unit Tests

```bash
python -m pytest tests/cluster tests/test_cloud_deploy.py tests/test_workspace_merge.py experiments/cephfs-s3fs-baselines/tests/test_baseline_suite.py
```

All personal identifiers, host aliases, internal IP addresses, private URLs,
and prior commit histories have been stripped for double-blind review.
