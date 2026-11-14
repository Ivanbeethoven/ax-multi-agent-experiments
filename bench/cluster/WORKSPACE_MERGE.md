# Native workspace merge

Run the adapter after cleanly unmounting both workspaces:

```text
python -m bench.cluster.workspace_merge --binary /path/to/brewfs \
  --meta-url redis://127.0.0.1:6379 --namespace experiment \
  --child CHILD --target TARGET --operation-file /path/to/merge-operation.json
```

The operation file is created exclusively and synchronized before BrewFS runs.
It binds an operation UUID to the Redis catalog, namespace, child and target.
Repeat the same command and operation file after interruption. The adapter
checks the response identity and forwards BrewFS's JSON result. Exit status is
0 for a committed merge, 2 for conflicts, and 1 for invocation or protocol errors.
The operation file may contain a credential-bearing Redis URL and is created
with owner-only permissions on POSIX systems.

This command operates on already provisioned workspaces. The experiment
coordinator owns mount shutdown, resource locks, post-merge remounts and cleanup.
BrewFS requires an identical native base and a shared object bucket for this
merge API. Its transaction preserves target-only changes and stores a durable
receipt for interrupted-command recovery.

Run the adapter tests with:

```text
python -m unittest discover -s tests -p test_workspace_merge.py -v
```
