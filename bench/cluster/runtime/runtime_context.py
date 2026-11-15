"""Paths and service ports for the selected experiment run."""
import json
from pathlib import Path

PATH = Path("/srv/ax-cluster/direct/runtime/context.json")
CONFIG = json.loads(PATH.read_text()) if PATH.exists() else {}
RUN_ID = CONFIG.get("run_id", "run-001")
RUN_ROOT = Path("/srv/ax-cluster/runs/direct") / RUN_ID
RUN_PREFIX = CONFIG.get("bucket_prefix", "axd1")
RUN_NAMESPACE = CONFIG.get("namespace_prefix", "axd1")
REDIS_PORT = int(CONFIG.get("redis_port", 16379))
PD_ENDPOINT = CONFIG.get("pd_endpoint", "http://175.168.3.55:2379")
