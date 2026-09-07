"""Hub demo/test fixtures (M2a Task 14): a small, deterministic,
self-contained dataset shared by ``tests/hub/test_fixtures.py`` and by
``create_default_app``'s optional ``NETHACKERS_LOAD_FIXTURES=1`` dev/demo
seeding (``compose.yaml``). See task-14-context.md, which governs.

``load_fixtures(store, *, now=...)`` is the only entry point and the only
thing in this module with a side effect -- importing it mutates nothing.
It upserts 2 catalog objectives (one per identity -- Task A3 dropped the
now-retired ``"random"`` objective's upsert; atoms don't reference the
catalog at all any more), 4 solutions (distinct digests/owners, one lineage
edge), and 11 atoms across them, then runs the same view-population call
``validate.register`` runs for a real registration (``update_attainment``)
so the store comes out fully populated -- atoms plus every derived view --
exactly as if these had all been registered for real. (``/elites`` is a
live query straight over ``atoms``, so there is nothing to additionally
populate for it -- Part 2 of the hub API redesign dropped the
``recompute_elites`` call this used to also run.)

Edge cases deliberately included, so the fixture exercises real system
behavior rather than a degenerate happy path:

- a ``milestone=None`` atom (GAMMA's second ``IDENTITY_B`` episode): a
  bot-failure/no-progress episode that ``update_attainment`` must skip
  entirely (task-7-context.md).
- an ``ascended=True`` atom (BETA's ``IDENTITY_B`` episode, seed=1,
  ``progression=1.0``): the deepest possible achievement, which -- per
  attainment.py's "a deep atom lights every shallower cell too" rule --
  lights ``IDENTITY_B``'s *entire* achievement ladder from one atom.
  Deliberately included so the fixture exercises that rule for real, not
  just in unit tests.
- a lineage edge: GAMMA -> ALPHA, kind ``"parent"`` (GAMMA is a fork of
  ALPHA).

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

Since Task 7, ``load_fixtures`` also seeds the private (verified) tier, so
the offline hub (``compose.override.yaml``, ``make hub``) has something to
show for ``?tier=verified`` instead of an empty Private Dungeons view.
Those atoms are a separate, deliberately asymmetric set: IDENTITY_A and
IDENTITY_B each get a verified AutoAscend floor, IDENTITY_C gets a verified
result and NO floor -- reproducing production's real "verified on more
identities than the floor covers yet" state, which is the case that must be
EXCLUDED from keepers/breakthroughs rather than credited against a floor of
0.0. They are inserted under ``secret_fingerprint(DEV_HIDDEN_SECRET)`` and
stamped with the real pinned ``ARENA_IMAGE`` (never the self-reported
tier's fake ``_EVALUATOR_IMAGE`` below) -- the hub filters every verified
read on exactly that secret/image pair, so getting either wrong makes the
fixture atoms silently invisible rather than loudly wrong.
``DEV_HIDDEN_SECRET``/``DEV_HIDDEN_SEEDS`` mirror ``compose.override.yaml``'s
``NETHACKERS_HIDDEN_SECRET``/``NETHACKERS_HIDDEN_SEEDS`` so the offline hub
process actually reads what these fixtures write -- pinned equal by
``tests/test_compose_stage.py``.
"""

from __future__ import annotations

from dataclasses import replace

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Atom, ResultStatus
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import update_attainment

# Two published identities (both already used elsewhere in the hub test
# suite as known-valid IDENTITIES members) -- the brief's "random + two
# identities", minus the "random" headline objective Task A1 retired.
IDENTITY_A = "val-dwa-law-fem"
IDENTITY_B = "wiz-elf-cha-mal"
IDENTITY_C = "mon-hum-neu-mal"

# Four solutions, distinct digests/owners/commits. Owners are real GitHub handles so
# the demo shows real avatars (the site ASCII-renders github.com/<owner>.png).
ALPHA = "sha256:fixture-alpha"
BETA = "sha256:fixture-beta"
GAMMA = "sha256:fixture-gamma"
DELTA = "sha256:fixture-delta"

_OWNER_BY_DIGEST: dict[str, str] = {
    ALPHA: "howuhh", BETA: "vkurenkov", GAMMA: "vlomshakov", DELTA: "cinemere",
}
_COMMIT_BY_DIGEST: dict[str, str] = {
    ALPHA: "a1" * 20,  # 40 well-formed (if fake) hex chars, distinct per solution
    BETA: "b2" * 20,
    GAMMA: "c3" * 20,
    DELTA: "d4" * 20,
}
_EVALUATOR_IMAGE = "nethackers/arena@sha256:" + "f" * 64

# Offline private-tier epoch. Mirrored by compose.override.yaml's
# NETHACKERS_HIDDEN_SECRET / NETHACKERS_HIDDEN_SEEDS -- pinned equal by
# tests/test_compose_stage.py. Fake values for dev only; production's live in
# GitHub secrets.
DEV_HIDDEN_SECRET = "offline-hidden-secret"
DEV_HIDDEN_SEEDS: tuple[int, ...] = (900001, 900002, 900003)


def load_fixtures(store: Store, *, now: str = "2026-01-01T00:00:00Z") -> None:
    """Populate ``store`` with the fixture dataset described in this
    module's docstring. Every underlying write (``upsert_solution``,
    ``add_lineage``, ``insert_atoms``, ``update_attainment``) is already
    idempotent, so re-running this against the same store is safe. Pure
    side effect on ``store`` -- importing this module does nothing.
    ``/elites`` is a live query over ``atoms``, so there is nothing left to
    populate for it here."""
    for digest in (ALPHA, BETA, GAMMA, DELTA):
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
        # --- IDENTITY_A: ALPHA is the ONLY solution that ever touches
        # IDENTITY_A, so every lit IDENTITY_A cell has exactly one holder.
        # Re-homed (Task A3) onto distinct seeds 0-3 -- two of these were
        # formerly under the now-retired "random" objective, and the new
        # UNIQUE(solution, identity, seed) key would otherwise collide them
        # with seeds 0/1 above. ---
        _atom(ALPHA, identity=IDENTITY_A, seed=0, milestone="Dlvl:10",
              ascended=False, turns=500, steps=800),
        _atom(ALPHA, identity=IDENTITY_A, seed=1, milestone="Dlvl:5",
              ascended=False, turns=300, steps=450),
        _atom(ALPHA, identity=IDENTITY_A, seed=2, milestone="Dlvl:6",
              ascended=False, turns=350, steps=500),
        _atom(ALPHA, identity=IDENTITY_A, seed=3, milestone="Dlvl:2",
              ascended=False, turns=150, steps=220),
        # --- IDENTITY_B. GAMMA is listed FIRST, so it wins the same-``now``
        # first-ratchet on any cell both it and BETA's (later-listed) atoms
        # also reach. ---
        _atom(GAMMA, identity=IDENTITY_B, seed=0, milestone="Dlvl:4",
              ascended=False, turns=250, steps=400),
        _atom(GAMMA, identity=IDENTITY_B, seed=1, progression=0.0, milestone=None,
              ascended=False, status="bot_timeout", turns=10, steps=15),
        # edge case above: milestone=None (a bot-failure episode) -- lights nothing
        _atom(BETA, identity=IDENTITY_B, seed=0, milestone="Dlvl:8",
              ascended=False, turns=600, steps=900),
        _atom(BETA, identity=IDENTITY_B, seed=1, milestone="You ascend t",
              ascended=True, turns=8000, steps=15000),
        # edge case above: an ascension -- lights IDENTITY_B's entire ladder
        _atom(BETA, identity=IDENTITY_B, seed=2, milestone="Dlvl:3",
              ascended=False, turns=200, steps=300),
        # --- IDENTITY_C: cinemere's DELTA, a Monk run on its own identity. ---
        _atom(DELTA, identity=IDENTITY_C, seed=0, milestone="Dlvl:6",
              ascended=False, turns=340, steps=480),
        _atom(DELTA, identity=IDENTITY_C, seed=1, milestone="Dlvl:3",
              ascended=False, turns=180, steps=260),
    ]
    store.insert_atoms(atoms)
    update_attainment(store, atoms, now=now)

    # --- Private tier (hidden seeds). IDENTITY_A and IDENTITY_B get a floor;
    # IDENTITY_C deliberately gets a result with NO floor, reproducing
    # production's "program result, no AutoAscend floor yet" state -- the case
    # that must be EXCLUDED from keepers/breakthroughs rather than credited
    # against 0.0. ---
    fingerprint = secret_fingerprint(DEV_HIDDEN_SECRET)
    verified = [
        _verified_atom(ALPHA, identity=IDENTITY_A, seed=s, milestone=m,
                       ascended=False, turns=500, steps=800)
        for s, m in zip(DEV_HIDDEN_SEEDS, ("Dlvl:8", "Dlvl:5", "Dlvl:6"), strict=True)
    ] + [
        _verified_atom(BETA, identity=IDENTITY_B, seed=s, milestone=m,
                       ascended=False, turns=600, steps=900)
        for s, m in zip(DEV_HIDDEN_SEEDS, ("Dlvl:4", "Dlvl:6", "Dlvl:3"), strict=True)
    ] + [
        _verified_atom(DELTA, identity=IDENTITY_C, seed=s, milestone=m,
                       ascended=False, turns=340, steps=480)
        for s, m in zip(DEV_HIDDEN_SEEDS, ("Dlvl:5", "Dlvl:6", "Dlvl:4"), strict=True)
    ]
    store.insert_verified_atoms(
        verified, secret_fingerprint=fingerprint,
        verifier_token_fingerprint="offline-verifier",
    )
    floor = [
        replace(
            _verified_atom(ALPHA, identity=IDENTITY_A, seed=s, milestone="Dlvl:3",
                           ascended=False, turns=200, steps=300),
            solution_digest="autoascend", owner="autoascend", tier="baseline",
        )
        for s in DEV_HIDDEN_SEEDS
    ] + [
        replace(
            _verified_atom(BETA, identity=IDENTITY_B, seed=s, milestone="Dlvl:2",
                           ascended=False, turns=150, steps=220),
            solution_digest="autoascend", owner="autoascend", tier="baseline",
        )
        for s in DEV_HIDDEN_SEEDS
    ]
    store.insert_verified_baseline_atoms(
        floor, secret_fingerprint=fingerprint,
        verifier_token_fingerprint="offline-verifier",
    )


def _atom(
    solution_digest: str,
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


def _verified_atom(
    solution_digest: str, *, identity: str, seed: int, milestone: str | None,
    ascended: bool, turns: int, steps: int, progression: float | None = None,
    status: ResultStatus = "completed",
) -> Atom:
    """One fixture atom for the private tier. Two differences from ``_atom``
    matter and both are load-bearing: ``tier="verified"``, and
    ``evaluator_image=ARENA_IMAGE`` -- the hub filters verified reads on the
    real pinned arena, so a fixture stamped with the fake ``_EVALUATOR_IMAGE``
    would be silently invisible to every private-tier view."""
    return replace(
        _atom(solution_digest, identity=identity, seed=seed, milestone=milestone,
              ascended=ascended, turns=turns, steps=steps, progression=progression,
              status=status),
        tier="verified", evaluator_image=ARENA_IMAGE,
    )
