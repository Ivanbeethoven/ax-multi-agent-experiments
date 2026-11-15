"""Run the matched harness with a separate, untimed Redis wire-command audit."""
from pathlib import Path
import sys

from redis_audit import audit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'overlay-open-a355-20261113'))
import run_detail


def main():
    latest = {}
    original_round = run_detail.measured_round

    def measured(root, expected):
        latest.update(root=root, expected=expected)
        return original_round(root, expected)

    def configure(module):
        commandstats, save = module.commandstats, module.save

        def stats(port):
            latest['port'] = port
            return commandstats(port)

        def saved(output, value):
            # All timed counters have already been captured at this boundary.
            # MONITOR/PING must not contaminate the standalone-lower zero-Redis
            # checks for mount, warmup or timed reads.
            if output.name == 'hot-read.json':
                assert latest['root'].name == output.parent.name
                audit(latest['port'], lambda: original_round(latest['root'], latest['expected']),
                      output.with_name('redis-wire-audit.json'))
            save(output, value)

        module.commandstats, module.save = stats, saved

    run_detail.measured_round = measured
    run_detail.main(configure_runner=configure,
                    extra_sources=(Path(__file__).resolve().with_name('redis_audit.py'),))


if __name__ == '__main__':
    main()
