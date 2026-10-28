# Three-node trace pilot

The controller reuses one exact owned build-only node, then adds at most one more private-only node after immutable assets are ready. The original node is the SSH bastion; private keys stay local. Existing extra public addresses are recorded and preserved during this active build. Each node runs all four normal-cache baselines in a rotated order. Fixed runner: `8e815bd91060bae90a2699668cc5d87e32b2e4c1`.

Keep private runtime files under ignored `runs/<batch>/`. Set `AX_PARALLEL_RUN_DIR` to that absolute folder before invoking `parallel_controller.py`; without `--execute` it only describes the plan. The folder contains the exact allocation state, reviewed `runner-ready.json`, and private transfer configuration. Never commit those files.

`PAUSED-FOR-CACHE-PLAN` stops provisioning and launches. A reviewed contract binds the four `*-kernel-default` arms, frozen runner commit and exact plan hash. Existing build-only allocations must be explicitly promoted into this contract; they are never silently recreated. Assets, source bundle, resource cap, recovery channel and worker launch authorization are checked before proceeding.

The RustFS build-only helpers export only approved public caches, verify tools/source/profile, preserve attempts, and atomically import the checked binary. The strict cargo handoff affects only the future exact RustFS command. The checkpoint reads `/proc` every 30 seconds during compilation and stores terminal evidence.

`cleanup_parallel.py` requires all three verified archives with exactly the planned terminal arms, matching receipts and successful credential scans. It refuses active compilers, replay/FS processes, or any FUSE mounts, and deletes only the recorded batch resources, including its keypair.

Offline checks: `python -m unittest discover -s infra/alicloud/parallel -p "test_*.py"`. These checks do not qualify cloud performance or a particular storage layout.

Private node-c uses a source-restricted dependency proxy on node-a's private address for GitHub branch checks and Ubuntu packages; OSS internal, localhost registry, and local data traffic bypass it. The proxy accepts only the recorded private client address and approved public dependency hosts, and never logs request URLs.
