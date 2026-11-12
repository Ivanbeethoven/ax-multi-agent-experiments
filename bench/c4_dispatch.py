"""Dispatch policy for retired historical workloads; plan validation stays unchanged."""

_NEW_WORK = {'observe-start', 'create', 'mount', 'measure'}


def retired_flat_fork(cell):
    arm = cell.get('arm') or {}
    return (cell.get('suite'), cell.get('workload'), cell.get('arm_id'),
            arm.get('product'), arm.get('engine')) == ('C4', 'fork', 'native', 'brewfs', 'flat-v1')


def require_allowed(cell, stage=None):
    if retired_flat_fork(cell) and (stage is None or stage in _NEW_WORK):
        raise ValueError('Historical C4 BrewFS Flat full-copy dispatch is retired; existing evidence remains readable')
