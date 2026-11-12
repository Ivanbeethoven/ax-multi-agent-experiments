"""Coordinate overlapping Aliyun pilot campaigns without editing their ledgers."""
from contextlib import closing, contextmanager
import json
from pathlib import Path
import re
import sqlite3

from .suite_plan import check_plan, parallel
from .runtime.pilot_pools import cell_resources, resource_guard


def physical(resources):
    """Only actual ECS instance and disk IDs establish physical overlap."""
    ids = {r for r in resources if r.startswith(("host:", "disk:"))}
    if (not any(r.startswith("host:") for r in ids)
            or any(not re.fullmatch(r"(?:host:i|disk:d)-[a-z0-9]+", r) for r in ids)):
        raise ValueError("actual Aliyun host/disk identities required")
    return ids


def reservations(path, expected=None):
    """Read one consistent SQLite snapshot and validate active reservation footprints."""
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise ValueError(f"regular local pilot ledger required: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        campaigns = db.execute("SELECT plan FROM campaign").fetchall()
        if len(campaigns) != 1:
            raise ValueError("exactly one ledger campaign required")
        plan = check_plan(json.loads(campaigns[0]["plan"]))
        if expected is not None and plan != expected:
            raise ValueError(f"ledger plan does not match expected campaign: {path}")
        cells = {c["id"]: c for c in plan["cells"]}
        blocks = {b["id"]: b for b in plan["blocks"]}
        cell_rows = {c["id"]: c for c in db.execute("SELECT id,block_id,status FROM cells")}
        block_rows = {b["id"]: b for b in db.execute("SELECT id,pool,status,owner,token FROM blocks")}
        if (set(cells) != set(cell_rows) or set(blocks) != set(block_rows)
                or any(row["block_id"] != cells[cid]["paired_block"]
                       or row["status"] not in ("pending", "claimed", "complete", "failed")
                       for cid, row in cell_rows.items())
                or any(row["pool"] != blocks[bid]["pool"] or row["status"] not in ("pending", "claimed", "done")
                       for bid, row in block_rows.items())):
            raise ValueError("ledger cells/blocks differ from its plan")

        active_cells = {cid for cid, row in cell_rows.items() if row["status"] == "claimed"}
        lease_rows = {row["cell_id"]: row for row in db.execute("SELECT cell_id,pool,owner,token FROM cell_leases")}
        cell_ids = footprints(db, "SELECT id,cell_id FROM cell_resources")
        block_ids = footprints(db, "SELECT id,block_id FROM resources")
        active = []
        if parallel(plan["config"]):
            if block_ids or set(lease_rows) != active_cells or set(cell_ids) != active_cells:
                raise ValueError("inconsistent parallel cell reservations")
            for cid, row in lease_rows.items():
                cell = cells[cid]
                if row["pool"] != cell["pool"] or cell_ids[cid] != set(cell_resources(plan, cell)):
                    raise ValueError("parallel cell lease footprint differs from its plan")
                active.append(reservation("cell", cid, row, cell_ids[cid], cid))
        else:
            claimed_blocks = {bid for bid, row in block_rows.items() if row["status"] == "claimed"}
            if (lease_rows or cell_ids or set(block_ids) != claimed_blocks
                    or any(cells[cid]["paired_block"] not in claimed_blocks for cid in active_cells)):
                raise ValueError("inconsistent paired block reservations")
            for bid in claimed_blocks:
                block = blocks[bid]
                claimed = active_cells.intersection(block["cells"])
                if len(claimed) > 1 or block_ids[bid] != set(block["resource_ids"]):
                    raise ValueError("paired block footprint differs from its plan")
                active.append(reservation("block", bid, block_rows[bid], block_ids[bid], next(iter(claimed), None)))
        return active


def footprints(db, query):
    found = {}
    for resource_id, reservation_id in db.execute(query):
        found.setdefault(reservation_id, set()).add(resource_id)
    return found


def reservation(kind, identity, row, resources, active_cell):
    if any(not isinstance(row[field], str) or not row[field] for field in ("owner", "token")):
        raise ValueError("active reservation lacks owner/token")
    return {"kind": kind, "identity": identity, "active_cell": active_cell,
            "pool": row["pool"], "owner": row["owner"], "token": row["token"],
            "physical": physical(resources)}


@contextmanager
def multi_ledger_guard(canonical, current, original_plan, current_plan, cell_id, *, ownership=None):
    """Hold canonical OS locks and reject conflicting old or new SQLite reservations.

    The two expected plans must be supplied by the caller's pinned inputs. All
    direct sibling state.sqlite ledgers are checked using their own saved plans.
    An optional (owner, token) admits only the exact currently claimed cell in
    the current ledger. Cooperating runners must acquire the same physical OS
    locks before claiming/using a resource; direct SQLite mutations are outside
    this guard. No database is initialized, changed, or released here.
    """
    canonical = Path(canonical).resolve(strict=True)
    current = Path(current).resolve(strict=True)
    if canonical == current or canonical.parent != current.parent or not current.is_dir():
        raise ValueError("distinct current and canonical sibling controls required")
    lock_dir = canonical / "runner-locks"
    if not lock_dir.is_dir() or lock_dir.is_symlink():
        raise ValueError("existing original canonical lock directory required")
    original_plan, current_plan = check_plan(original_plan), check_plan(current_plan)
    if original_plan["plan_sha256"] == current_plan["plan_sha256"]:
        raise ValueError("current campaign plan must differ from the original")
    selected = [cell for cell in current_plan["cells"] if cell["id"] == cell_id]
    if len(selected) != 1:
        raise ValueError("exact current-plan cell required")
    cell = selected[0]
    wanted = physical(cell_resources(current_plan, cell))
    if ownership is not None and (not isinstance(ownership, tuple) or len(ownership) != 2
                                  or any(not isinstance(v, str) or not v for v in ownership)):
        raise ValueError("exact (owner, token) pair required")
    expected = {canonical / "state.sqlite": original_plan, current / "state.sqlite": current_plan}

    with resource_guard(canonical, cell, current_plan):
        paths = sorted(canonical.parent.glob("*/state.sqlite"))
        if not expected.keys() <= set(paths):
            raise ValueError("missing original or current ledger")
        found_owner = False
        for path in paths:
            try:
                rows = reservations(path, expected.get(path))
            except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
                raise ValueError(f"invalid pilot ledger {path}: {exc}") from exc
            for row in rows:
                overlap = wanted.intersection(row["physical"])
                if not overlap:
                    continue
                if (path == current / "state.sqlite" and row["active_cell"] == cell_id
                        and ownership == (row["owner"], row["token"])):
                    found_owner = True
                    continue
                raise ValueError(
                    f"physical resources reserved by {row['kind']} {row['identity']} "
                    f"in {path}: {', '.join(sorted(overlap))}"
                )
        if ownership is not None and not found_owner:
            raise ValueError("current ledger has no exact reservation for this cell and ownership")
        if sorted(canonical.parent.glob("*/state.sqlite")) != paths:
            raise ValueError("pilot ledger set changed during guard check")
        yield tuple(paths)
