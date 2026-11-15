#!/bin/bash
set -euo pipefail
job=/home/dev/ax-five-fixes-20261112
/bin/bash "$job/build-02.sh" "$job" "$job/candidate-03"
python3 "$job/write-provenance.py" "$job/candidate-03"
exec /usr/bin/flock /home/dev/.ax-rbm-heavy.lock /bin/bash "$job/run-ablations-03.sh"
