#!/usr/bin/env python3
"""Predict whether a C4 observation series can be archived at all.

The frozen archive checker keeps one JSON manifest with one entry per file and
refuses anything above 16 MiB. A five-second series over five hours produced
132,944 entries and a 20,430,785-byte manifest, so the bound was only discovered
after the measurement had already run. This tool answers the same question
before a window starts:

    python3 plan-c4-observer-budget.py --seconds 18000 --interval 5

and, for a bound, the coarsest interval that still fits:

    python3 plan-c4-observer-budget.py --seconds 18000 --interval 5 --fit 16777216

The per-member cost is measured, not guessed: one entry is the file name, its
size and its SHA256 in the frozen manifest's own JSON shape.
"""
import argparse
import json

MANIFEST_BOUND = 16*1024*1024
MEMBERS_PER_SAMPLE = 45
KNOWN = dict(seconds=15385.08, interval=5.0, samples=2954, tree_members=132939,
             manifest_bytes=20430785, archive_members=132944, per_sample=45, extra_members=9)


def entry_bytes(name, size):
    """One frozen-manifest entry: the file name, its size and its digest."""
    return len(json.dumps({'bytes': size, 'name': name,
                           'sha256': '0'*64}, separators=(',', ':')))+1


def predict(seconds, interval, members_per_sample=MEMBERS_PER_SAMPLE, extra=KNOWN['extra_members']):
    """Members and manifest bytes for one series, using the measured shape."""
    if seconds <= 0 or interval <= 0:
        raise ValueError('seconds and interval must be positive')
    samples = int(seconds//interval)+1
    members = samples*members_per_sample+extra
    per_member = KNOWN['manifest_bytes']/KNOWN['archive_members']
    return dict(seconds=seconds, interval=interval, samples=samples, members=members,
                manifest_bytes=int(members*per_member),
                per_member_bytes=per_member)


def fits(seconds, members_per_sample=MEMBERS_PER_SAMPLE, extra=KNOWN['extra_members'], bound=MANIFEST_BOUND):
    """Coarsest interval that keeps the manifest inside the bound."""
    lo, hi = 1.0, max(seconds, 2.0)
    def members_for(interval):
        return predict(seconds, interval, members_per_sample, extra)['manifest_bytes']
    if members_for(hi) > bound:
        return None
    while hi-lo > 0.01:
        mid = (lo+hi)/2
        if members_for(mid) <= bound:
            hi = mid
        else:
            lo = mid
    return hi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--interval', type=float, default=5.0)
    parser.add_argument('--members-per-sample', type=int, default=MEMBERS_PER_SAMPLE)
    parser.add_argument('--extra-members', type=int, default=KNOWN['extra_members'])
    parser.add_argument('--bound', type=int, default=MANIFEST_BOUND)
    args = parser.parse_args()
    row = predict(args.seconds, args.interval, args.members_per_sample, args.extra_members)
    row['fits'] = row['manifest_bytes'] <= args.bound
    row['bound'] = args.bound
    row['measured_reference'] = KNOWN
    if not row['fits']:
        interval = fits(args.seconds, args.members_per_sample, args.extra_members, args.bound)
        row['coarsest_interval_that_fits'] = interval
        row['verdict'] = ('this series cannot be archived under the frozen bound at %.1f s; '
                          'it needs at least %.2f s' % (args.interval, interval)) if interval else \
                         ('this series cannot be archived under the frozen bound at any interval')
    else:
        row['verdict'] = 'this series fits the frozen bound'
    print(json.dumps(row, indent=2))


if __name__ == '__main__':
    main()
