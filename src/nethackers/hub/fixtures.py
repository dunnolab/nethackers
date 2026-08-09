"""Hub demo/test fixtures (M2a Task 14): a small, deterministic,
self-contained dataset shared by ``tests/hub/test_fixtures.py`` and by
``create_default_app``'s optional ``NETHACKERS_LOAD_FIXTURES=1`` dev/demo
seeding (``compose.yaml``). See task-14-context.md, which governs.

``load_fixtures(store, *, now=...)`` is the only entry point and the only
thing in this module with a side effect -- importing it mutates nothing.
It upserts 3 catalog objectives ("random" plus two identities), 3
solutions (distinct digests/owners, one lineage edge), and 9 atoms across
them, then runs the same view-population calls ``validate.register`` runs
for a real registration (``update_attainment``, ``recompute_elites``) so
the store comes out fully populated -- atoms plus every derived view --
exactly as if these had all been registered for real.

Edge cases deliberately included, so the fixture exercises real system
behavior rather than a degenerate happy path:

- a ``milestone=None`` atom (GAMMA's second ``IDENTITY_B`` episode): a
  bot-failure/no-progress episode that ``update_attainment`` must skip
  entirely (task-7-context.md).
- an ``ascended=True`` atom (BETA's ``"random"``-objective ``IDENTITY_B``
  episode, ``progression=1.0``): the deepest possible achievement, which
  -- per attainment.py's "a deep atom lights every shallower cell too"
  rule -- lights ``IDENTITY_B``'s *entire* achievement ladder from one
  atom. Deliberately included so the fixture exercises that rule for real,
  not just in unit tests.
- a lineage edge: GAMMA -> ALPHA, kind ``"parent"`` (GAMMA is a fork of
  ALPHA).
- distinct ascension counts for ``"random"``'s two participating solutions
  (ALPHA: 0, BETA: 1), so ``board(..., objective="random")``'s ranking is
  unambiguous under ``asc_median_mean`` without ever needing a
  ``created_at`` tiebreak.

Determinism: every atom's ``progression`` here is either a literal or an
``ACHIEVEMENTS[...]`` lookup (itself a frozen module-level constant), and
``now`` defaults to a fixed timestamp -- so two calls to
``load_fixtures(Store(...))`` against fresh stores always produce
byte-identical ``/attainment``/``/board`` reads. The atoms list's order is
also significant and fixed: several ``IDENTITY_B`` cells are reachable by
both GAMMA and BETA, and ``update_attainment``'s "first" ratchet only ever
moves to a *strictly earlier* ``first_at`` (task-7-context.md) -- so with
every atom in one call sharing the same ``now``, ties resolve to whichever
atom this module lists first (GAMMA, deliberately listed before BETA's
``IDENTITY_B`` atoms), not to insertion/DB order.
"""

from __future__ import annotations

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.contracts.models import Atom, ObjectiveSpec, ResultStatus
from nethackers.hub.objectives import CATALOG
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import update_attainment
from nethackers.hub.views.elites import recompute_elites

# Two published identities (both already used elsewhere in the hub test
# suite as known-valid IDENTITIES members) plus the "random" headline
# objective -- the brief's "random + two identities".
IDENTITY_A = "val-dwa-law-fem"
IDENTITY_B = "wiz-elf-cha-mal"

# Three solutions, distinct digests/owners/commits.
ALPHA = "sha256:fixture-alpha"
BETA = "sha256:fixture-beta"
GAMMA = "sha256:fixture-gamma"

_OWNER_BY_DIGEST: dict[str, str] = {ALPHA: "alice", BETA: "bob", GAMMA: "carol"}
_COMMIT_BY_DIGEST: dict[str, str] = {
    ALPHA: "a1" * 20,  # 40 well-formed (if fake) hex chars, distinct per solution
    BETA: "b2" * 20,
    GAMMA: "c3" * 20,
}
_EVALUATOR_IMAGE = "nethackers/arena@sha256:" + "f" * 64


def load_fixtures(store: Store, *, now: str = "2026-01-01T00:00:00Z") -> None:
    """Populate ``store`` with the fixture dataset described in this
    module's docstring. Every underlying write (``objectives_upsert``,
    ``upsert_solution``, ``add_lineage``, ``insert_atoms``,
    ``update_attainment``, ``recompute_elites``) is already idempotent, so
    re-running this against the same store is safe. Pure side effect on
    ``store`` -- importing this module does nothing."""
    objective_a = CATALOG[IDENTITY_A]
    objective_b = CATALOG[IDENTITY_B]
    objective_random = CATALOG["random"]
    for spec in (objective_a, objective_b, objective_random):
        store.objectives_upsert(spec)

    for digest in (ALPHA, BETA, GAMMA):
        owner = _OWNER_BY_DIGEST[digest]
        store.upsert_solution(
            digest,
            repo=f"github.com/{owner}/nethacker",
            commit_sha=_COMMIT_BY_DIGEST[digest],
            owner=owner,
            root=".",
            entrypoint="bot.py",
            registered_at=now,
        )
    store.add_lineage(GAMMA, ALPHA, "parent")  # GAMMA is a fork of ALPHA

    atoms = [
        # --- IDENTITY_A under its own published objective: ALPHA is the
        # ONLY solution that ever touches IDENTITY_A (here or under
        # "random" below), so every lit IDENTITY_A cell has exactly one
        # holder. ---
        _atom(
            ALPHA, objective_a, identity=IDENTITY_A, seed=0,
            milestone="Dlvl:10", ascended=False, turns=500, steps=800,
        ),
        _atom(
            ALPHA, objective_a, identity=IDENTITY_A, seed=1,
            milestone="Dlvl:5", ascended=False, turns=300, steps=450,
        ),
        # --- IDENTITY_B under its own published objective. GAMMA is
        # listed FIRST, so it wins the same-``now`` first-ratchet on any
        # cell both it and BETA's (later-listed) atoms also reach. ---
        _atom(
            GAMMA, objective_b, identity=IDENTITY_B, seed=0,
            milestone="Dlvl:4", ascended=False, turns=250, steps=400,
        ),
        _atom(
            GAMMA, objective_b, identity=IDENTITY_B, seed=1,
            progression=0.0, milestone=None, ascended=False,
            status="bot_timeout", turns=10, steps=15,
        ),  # edge case: milestone=None (a bot-failure episode) -- lights nothing
        _atom(
            BETA, objective_b, identity=IDENTITY_B, seed=0,
            milestone="Dlvl:8", ascended=False, turns=600, steps=900,
        ),
        # --- "random" headline objective: ALPHA (0 ascensions) vs BETA (1
        # ascension) -- distinct ascension counts make board(random)'s
        # ranking unambiguous without any mean/median/digest tiebreak. ---
        _atom(
            ALPHA, objective_random, identity=IDENTITY_A, seed=0,
            milestone="Dlvl:6", ascended=False, turns=350, steps=500,
        ),
        _atom(
            ALPHA, objective_random, identity=IDENTITY_A, seed=1,
            milestone="Dlvl:2", ascended=False, turns=150, steps=220,
        ),
        _atom(
            BETA, objective_random, identity=IDENTITY_B, seed=0,
            milestone="You ascend t", ascended=True, turns=8000, steps=15000,
        ),  # edge case: an ascension -- lights IDENTITY_B's entire ladder
        _atom(
            BETA, objective_random, identity=IDENTITY_B, seed=1,
            milestone="Dlvl:3", ascended=False, turns=200, steps=300,
        ),
    ]
    store.insert_atoms(atoms)
    update_attainment(store, atoms, now=now)
    recompute_elites(store)


def _atom(
    solution_digest: str,
    objective: ObjectiveSpec,
    *,
    identity: str,
    seed: int,
    milestone: str | None,
    ascended: bool,
    turns: int,
    steps: int,
    progression: float | None = None,
    status: ResultStatus = "completed",
) -> Atom:
    """One fixture atom. ``progression`` defaults to
    ``ACHIEVEMENTS[milestone]`` -- the exact threshold value, i.e. an
    episode read at the moment it crossed that milestone -- and must be
    passed explicitly when ``milestone is None`` (there is no achievement
    value to default from)."""
    if progression is None:
        if milestone is None:
            raise ValueError("progression is required when milestone is None")
        progression = ACHIEVEMENTS[milestone]
    return Atom(
        solution_digest=solution_digest,
        objective_digest=objective.digest(),
        owner=_OWNER_BY_DIGEST[solution_digest],
        tier="self-reported",
        identity=identity,
        seed=seed,
        progression=progression,
        milestone=milestone,
        ascended=ascended,
        status=status,
        turns=turns,
        steps=steps,
        evaluator_image=_EVALUATOR_IMAGE,
    )
