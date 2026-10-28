#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
# Old --config c2/... arguments are deliberately rejected, not reinterpreted.
exec python3 "$ROOT/harness/aliyun_suite.py" "$@"
