#!/usr/bin/env python3
"""Freeze a paired C2 release schedule; never starts processes or sleeps."""

import argparse
import hashlib

from experiment_io import write_new

WINDOW_MS = 2000
ALGORITHM = "sha256-stratified-release-v1"


def schedule(count, seed, mode):
    if type(count) is not int or count not in (8, 16, 32):
        raise ValueError("formal cohort size must be 8, 16, or 32")
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("seed must be an unsigned 64-bit integer")
    if mode not in ("burst", "staggered"):
        raise ValueError("unknown release mode")

    def key(label, agent):
        return hashlib.sha256(f"{ALGORITHM}:{label}:{seed}:{agent}".encode()).digest()

    agents = [f"a{i:03}" for i in range(1, count + 1)]
    offsets = dict.fromkeys(agents, 0)
    if mode == "staggered":
        order = sorted(agents, key=lambda agent: (key("order", agent), agent))
        for i, agent in enumerate(order[1:], 1):
            low, high = i * WINDOW_MS // count, (i + 1) * WINDOW_MS // count
            offsets[agent] = low + int.from_bytes(key("offset", agent), "big") % (high - low)
    return {
        "schema": "trace-start-schedule-v1",
        "execution_enabled": False,
        "algorithm": ALGORITHM,
        "mode": mode,
        "agent_count": count,
        "seed": seed,
        "offset_basis": "milliseconds after common ready-barrier release",
        "window_ms": WINDOW_MS if mode == "staggered" else 0,
        "agents": [{"agent_id": a, "start_offset_ms": offsets[a]} for a in agents],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, choices=(8, 16, 32), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--mode", choices=("burst", "staggered"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_new(args.output, schedule(args.agents, args.seed, args.mode))


if __name__ == "__main__":
    main()
