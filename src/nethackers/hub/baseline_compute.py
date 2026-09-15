"""Compute AutoAscend's baseline atoms by running it through the arena on each
objective, storing them in the isolated baseline_atoms table. AutoAscend is the
reference floor, not a participant: owner/solution="autoascend", tier="baseline",
and the atoms never rank in boards/elites (separate table). Idempotent per
identity (Task A3: baseline_atoms has no objective_digest either) so a re-run
(arena or objectives changed) replaces, never doubles.

``--image`` is held to register's own admission rule (spec 2026-09-14 D5): it
must be a classified arena digest at the current ``ARENA_MAJOR``. This is the
only path that writes public-board numbers without passing through
``hub/validate.register``, and what it writes is the FLOOR every other score on
that board is read against, so an unclassified image here would quietly
undo the guarantee admission exists to provide."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime

from nethackers.arena_version import ARENA_MAJOR
from nethackers.contracts.models import Evidence, ObjectiveSpec
from nethackers.harness.evaluate import evaluate as _evaluate
from nethackers.hub.atoms import evidence_to_atoms
from nethackers.hub.ids import AUTOASCEND_ID, AUTOASCEND_TREE
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.validate import RegisterError, classified_major


def compute_baseline(store: Store, specs: Iterable[ObjectiveSpec], *, image: str, now: str,
                     tree: str = AUTOASCEND_TREE,
                     evaluate_fn: Callable[..., tuple[float, Evidence]] = _evaluate,
                     runner=subprocess.run) -> int:
    """Evaluate AutoAscend on each spec and store the atoms as the baseline.
    Returns the number of atoms inserted this call.

    ``image`` must be a classified arena image at the current ``ARENA_MAJOR``
    (spec 2026-09-14 D5). This path writes to ``baseline_atoms`` DIRECTLY --
    it never goes through ``register``, so it is the one way a number measured
    on some other arena could reach the public board, and the reference FLOOR
    every score on that board is read against. Checked once, up front: the
    full recompute is hours long, and finding out at the end is finding out
    too late.
    """
    classified_major(image, ARENA_MAJOR)
    total = 0
    for spec in specs:
        _mean, evidence = evaluate_fn(tree, spec, image, now=now, runner=runner)
        atoms = [replace(atom, tier="baseline")
                 for atom in evidence_to_atoms(evidence, owner=AUTOASCEND_ID,
                                               solution_id=AUTOASCEND_ID)]
        # Idempotent recompute: drop this objective's prior baseline rows,
        # keyed by identity now that objective_digest is gone. The DELETE(s)
        # open the transaction insert_baseline_atoms' own `with self._conn:`
        # commits -- DELETE and inserts land (or roll back) together.
        for ident in sorted({character for _seed, character in spec.batch}):
            store.conn.execute("DELETE FROM baseline_atoms WHERE identity = ?", (ident,))
        total += store.insert_baseline_atoms(atoms)
    return total


def main() -> None:  # pragma: no cover - thin CLI wiring over compute_baseline
    parser = argparse.ArgumentParser(description="Compute AutoAscend baseline atoms.")
    parser.add_argument("--db", required=True, help="path to the hub sqlite db")
    parser.add_argument("--image", required=True,
                        help="arena image -- the pinned digest classified at the current "
                             "ARENA_MAJOR; a local tag is refused")
    parser.add_argument("--identity", action="append",
                        help="identity to compute (repeatable); default = all identity objectives")
    args = parser.parse_args()
    store = Store(args.db)
    store.init_schema()
    names = args.identity or [n for n, s in CATALOG.items() if s.kind == "identity"]
    specs = [CATALOG[name] for name in names]
    now = datetime.now(UTC).isoformat()
    try:
        n = compute_baseline(store, specs, image=args.image, now=now)
    except RegisterError as error:  # unclassified / retired-major arena image
        raise SystemExit(f"refusing to compute the baseline: {error}") from None
    print(f"inserted {n} baseline atoms across {len(specs)} objective(s)")


if __name__ == "__main__":  # pragma: no cover
    main()
