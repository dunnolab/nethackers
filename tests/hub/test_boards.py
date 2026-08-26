"""Tests for ``nethackers.hub.views.boards``: the pure, on-read grading-
functional boards (M2a Task 9) -- a ranking is a grading functional over
atoms + a tier filter, computed fresh on every call (nothing stored), plus
coverage/firsts boards over the attainment record. See task-9-context.md --
the crux is its RESOLUTION over the brief's wording: ``board`` filters atoms
by ``objective_digest`` for a concrete objective (``batch`` non-empty), never
by "characters" -- two objectives can target the same identity/character yet
be scored on different published batches, and lumping their atoms together
would break the same-batch comparability guarantee (spec Sec2). The
functional ``"all"`` (``batch == ()``) is the one deliberate exception -- a
breadth rollup over every atom at a tier, regardless of which objective
produced it.
"""

from __future__ import annotations

import pytest

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.contracts.models import Atom, ObjectiveSpec
from nethackers.hub.objectives import CATALOG, IDENTITIES
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import update_attainment
from nethackers.hub.views.boards import (
    aggregate_board,
    board,
    coverage_board,
    firsts_board,
    resolve_scope,
)

IDENTITY = "val-dwa-law-fem"
OTHER_IDENTITY = "wiz-elf-cha-fem"


def _spec(**overrides):
    fields = dict(
        name="test-objective",
        kind="identity",
        batch=((0, IDENTITY), (1, IDENTITY)),
        max_steps=1000,
        no_progress_timeout=100,
        action_timeout_seconds=5.0,
        aggregation="asc_median_mean",
    )
    fields.update(overrides)
    return ObjectiveSpec(**fields)


def _atom(objective, **overrides):
    fields = dict(
        solution_digest="sha256:solution-a",
        objective_digest=objective.digest(),
        owner="sam",
        tier="self-reported",
        identity=IDENTITY,
        seed=0,
        progression=0.5,
        milestone=None,
        ascended=False,
        status="completed",
        turns=5,
        steps=10,
        evaluator_image="img@sha256:x",
    )
    fields.update(overrides)
    return Atom(**fields)


def _new_store(tmp_path):
    store = Store(tmp_path / "hub.sqlite3")
    store.init_schema()
    return store


def _seed(store: Store, atoms: list[Atom], specs: list[ObjectiveSpec]) -> None:
    """Seed every FK parent ``insert_atoms`` needs -- one ``objectives`` row
    per distinct objective an atom references, and one ``solutions`` row per
    distinct solution -- then insert the atoms themselves (task-9-context.md's
    setup note; mirrors test_elites.py's ``_seed``)."""
    for spec in specs:
        store.objectives_upsert(spec)
    for digest in {atom.solution_digest for atom in atoms}:
        store.upsert_solution(
            digest,
            repo="r",
            commit_sha="c",
            owner="sam",
            root=".",
            entrypoint="bot.py",
            registered_at="2026-01-01T00:00:00Z",
        )
    store.insert_atoms(atoms)


def test_asc_median_mean_ranks_by_ascensions_then_median_then_mean_not_by_mean_alone(tmp_path):
    # Property 1 (the tie-rule): solution A has a LOWER mean progression but
    # MORE ascensions than B -- under plain "mean" B would outrank A, but
    # asc_median_mean's lexicographic (ascensions -> median -> mean) key must
    # still rank A first. Proves the rule is genuinely lexicographic, not a
    # mean-only ranking in disguise.
    store = _new_store(tmp_path)
    spec = _spec(aggregation="asc_median_mean")
    atoms = [
        _atom(spec, solution_digest="sha256:a", seed=0, ascended=True, progression=0.5),
        _atom(spec, solution_digest="sha256:a", seed=1, ascended=False, progression=0.3),
        _atom(spec, solution_digest="sha256:b", seed=0, ascended=False, progression=0.9),
        _atom(spec, solution_digest="sha256:b", seed=1, ascended=False, progression=0.9),
    ]
    _seed(store, atoms, [spec])

    entries = board(store, spec)

    by_digest = {e["solution_digest"]: e for e in entries}
    # The disagreement, made explicit: B's plain mean is higher than A's.
    assert by_digest["sha256:b"]["mean_progression"] > by_digest["sha256:a"]["mean_progression"]
    # Yet asc_median_mean ranks A first, because A has more ascensions.
    assert [e["solution_digest"] for e in entries] == ["sha256:a", "sha256:b"]
    assert [e["rank"] for e in entries] == [1, 2]
    assert by_digest["sha256:a"]["ascensions"] == 1
    assert by_digest["sha256:b"]["ascensions"] == 0
    assert by_digest["sha256:a"]["median_progression"] == 0.4
    assert by_digest["sha256:a"]["mean_progression"] == 0.4
    assert by_digest["sha256:a"]["episodes"] == 2
    assert by_digest["sha256:a"]["owner"] == "sam"


def test_mean_aggregation_ranks_purely_by_mean_progression(tmp_path):
    # Property 2: an objective with aggregation="mean" ranks by mean
    # progression descending -- ignoring ascensions entirely ("low" has
    # ascended=True but still ranks below "high", which never ascended).
    store = _new_store(tmp_path)
    spec = _spec(aggregation="mean")
    atoms = [
        _atom(spec, solution_digest="sha256:high", seed=0, progression=0.9, ascended=False),
        _atom(spec, solution_digest="sha256:low", seed=0, progression=0.2, ascended=True),
    ]
    _seed(store, atoms, [spec])

    entries = board(store, spec)

    assert [e["solution_digest"] for e in entries] == ["sha256:high", "sha256:low"]
    assert [e["rank"] for e in entries] == [1, 2]
    assert entries[0]["mean_progression"] == 0.9
    assert entries[1]["mean_progression"] == 0.2


def test_objective_digest_isolation_a_different_objectives_atoms_do_not_leak_in(tmp_path):
    # Property 3: two objectives target the SAME identity/character but have
    # different digests (different name) -- a solution's atoms produced
    # under the OTHER objective must not appear on this objective's board,
    # even though both share ``identity``. Proves digest-filtering, not
    # character-filtering (task-9-context.md's RESOLUTION).
    store = _new_store(tmp_path)
    spec_a = _spec(name="objective-a", batch=((0, IDENTITY),))
    spec_b = _spec(name="objective-b", batch=((0, IDENTITY),))
    assert spec_a.digest() != spec_b.digest()

    atoms = [
        _atom(spec_a, solution_digest="sha256:under-a", seed=0, progression=0.5),
        _atom(spec_b, solution_digest="sha256:under-b", seed=0, progression=0.5),
    ]
    _seed(store, atoms, [spec_a, spec_b])

    entries = board(store, spec_a)

    assert [e["solution_digest"] for e in entries] == ["sha256:under-a"]
    assert "sha256:under-b" not in [e["solution_digest"] for e in entries]


def test_all_rollup_aggregates_across_every_objectives_atoms(tmp_path):
    # Property 4: the functional "all" (batch == ()) is a breadth rollup --
    # a solution's atoms from TWO DIFFERENT concrete objectives (on two
    # different identities) both count toward its one aggregated entry, not
    # filtered to a single objective/batch.
    store = _new_store(tmp_path)
    spec_x = _spec(name="objective-x", batch=((0, IDENTITY),))
    spec_y = _spec(name="objective-y", batch=((0, OTHER_IDENTITY),))
    atoms = [
        _atom(
            spec_x, solution_digest="sha256:multi", identity=IDENTITY, seed=0,
            progression=0.6, ascended=False,
        ),
        _atom(
            spec_y, solution_digest="sha256:multi", identity=OTHER_IDENTITY, seed=0,
            progression=0.8, ascended=True,
        ),
        _atom(
            spec_x, solution_digest="sha256:single", identity=IDENTITY, seed=1,
            progression=0.5, ascended=False,
        ),
    ]
    _seed(store, atoms, [spec_x, spec_y])

    entries = board(store, CATALOG["all"])

    by_digest = {e["solution_digest"]: e for e in entries}
    assert by_digest["sha256:multi"]["episodes"] == 2  # crosses objective boundaries
    assert by_digest["sha256:multi"]["ascensions"] == 1
    assert [e["solution_digest"] for e in entries] == ["sha256:multi", "sha256:single"]


def test_coverage_board_counts_cells_held_per_solution(tmp_path):
    # Property 5: coverage_board counts distinct (identity, milestone) cells
    # each solution holds, via attainment_holders. Expected counts are
    # derived from ACHIEVEMENTS itself (the same "value <= threshold" rule
    # update_attainment uses), not hardcoded, so the test can't silently
    # drift if the achievement ladder gains/loses labels.
    store = _new_store(tmp_path)
    spec = _spec()
    atom_a = _atom(
        spec, solution_digest="sha256:a", seed=0,
        progression=ACHIEVEMENTS["Dlvl:5"], milestone="Dlvl:5",
    )
    atom_b = _atom(
        spec, solution_digest="sha256:b", seed=1,
        progression=ACHIEVEMENTS["Dlvl:2"], milestone="Dlvl:2",
    )
    _seed(store, [atom_a, atom_b], [spec])
    update_attainment(store, [atom_a, atom_b], now="2026-01-01T00:00:00Z")

    expected_a = sum(1 for v in ACHIEVEMENTS.values() if v <= ACHIEVEMENTS["Dlvl:5"])
    expected_b = sum(1 for v in ACHIEVEMENTS.values() if v <= ACHIEVEMENTS["Dlvl:2"])
    assert expected_a > expected_b  # sanity: Dlvl:5 must actually cover more cells

    entries = coverage_board(store)

    by_digest = {e["solution_digest"]: e for e in entries}
    assert by_digest["sha256:a"]["cells_held"] == expected_a
    assert by_digest["sha256:b"]["cells_held"] == expected_b
    assert [e["solution_digest"] for e in entries] == ["sha256:a", "sha256:b"]
    assert [e["rank"] for e in entries] == [1, 2]
    assert by_digest["sha256:a"]["owner"] == "sam"


def test_firsts_board_counts_only_the_earliest_holder_per_cell(tmp_path):
    # Property 6: firsts_board counts cells a solution was FIRST to reach --
    # a later reacher of the SAME cell must not count toward its firsts, and
    # must not appear in the board at all (it was never anyone's first).
    store = _new_store(tmp_path)
    spec = _spec()
    milestone = "Dlvl:3"
    progression = ACHIEVEMENTS[milestone]
    early = _atom(
        spec, solution_digest="sha256:early", owner="early-owner", seed=0,
        progression=progression, milestone=milestone,
    )
    late = _atom(
        spec, solution_digest="sha256:late", owner="late-owner", seed=1,
        progression=progression, milestone=milestone,
    )
    _seed(store, [early, late], [spec])
    update_attainment(store, [early], now="2026-01-01T00:00:00Z")
    update_attainment(store, [late], now="2026-01-02T00:00:00Z")

    entries = firsts_board(store)

    assert len(entries) == 1
    assert entries[0]["solution_digest"] == "sha256:early"
    assert entries[0]["owner"] == "early-owner"
    assert entries[0]["rank"] == 1
    expected_firsts = sum(1 for v in ACHIEVEMENTS.values() if v <= progression)
    assert entries[0]["firsts"] == expected_firsts


def test_board_on_objective_with_no_atoms_is_empty(tmp_path):
    # Property 7: no atoms have ever been inserted for this objective ->
    # board() returns [] rather than erroring.
    store = _new_store(tmp_path)
    spec = _spec(name="unused-objective")

    assert board(store, spec) == []


def test_board_raises_on_unknown_aggregation(tmp_path):
    # Self-review requirement: an objective naming an aggregation board()
    # doesn't implement must raise, not silently no-op or crash obscurely.
    store = _new_store(tmp_path)
    spec = _spec(aggregation="not-a-real-aggregation")

    with pytest.raises(ValueError, match="not-a-real-aggregation"):
        board(store, spec)


# --- resolve_scope (Task 2) -------------------------------------------------


def test_resolve_scope_generalist_is_all_73():
    kind, ids = resolve_scope("generalist")
    assert kind == "generalist"
    assert set(ids) == set(IDENTITIES)
    assert len(ids) == 73


def test_resolve_scope_role_is_that_roles_identities():
    kind, ids = resolve_scope("val")
    assert kind == "role"
    assert set(ids) == {i for i in IDENTITIES if i.startswith("val-")}
    assert all(i.startswith("val-") for i in ids)


def test_resolve_scope_identity_is_singleton():
    kind, ids = resolve_scope("val-dwa-law-fem")
    assert kind == "identity"
    assert ids == ("val-dwa-law-fem",)


def test_resolve_scope_unknown_raises():
    with pytest.raises(ValueError):
        resolve_scope("not-a-thing")


# --- deepest on board() (Task 3) --------------------------------------------


def test_board_row_includes_deepest_milestone(tmp_path):
    store = _new_store(tmp_path)
    spec = _spec(aggregation="mean")
    atoms = [
        _atom(spec, solution_digest="sha256:s", seed=0,
              progression=ACHIEVEMENTS["Dlvl:5"], milestone="Dlvl:5"),
        _atom(spec, solution_digest="sha256:s", seed=1,
              progression=ACHIEVEMENTS["Dlvl:2"], milestone="Dlvl:2"),
    ]
    _seed(store, atoms, [spec])
    (entry,) = board(store, spec)
    assert entry["deepest"] == "Dlvl:5"


# --- aggregate_board (Task 4) -----------------------------------------------

VAL_IDS = ["val-dwa-law-fem", "val-hum-law-fem", "val-hum-neu-fem"]  # the 3 Valkyrie identities


def test_aggregate_board_is_coverage_first_then_mean(tmp_path):
    # broad-shallow (covers 3 ids @0.2) must outrank narrow-deep (1 id @0.9)
    store = _new_store(tmp_path)
    specs = [CATALOG[i] for i in VAL_IDS]
    atoms = [
        _atom(CATALOG[i], solution_digest="sha256:broad", identity=i, seed=0, progression=0.2)
        for i in VAL_IDS
    ]
    atoms.append(
        _atom(CATALOG[VAL_IDS[0]], solution_digest="sha256:narrow",
              identity=VAL_IDS[0], seed=0, progression=0.9)
    )
    _seed(store, atoms, specs)

    rows = aggregate_board(store, VAL_IDS)

    assert [r["solution_digest"] for r in rows] == ["sha256:broad", "sha256:narrow"]
    assert rows[0]["coverage"] == 3 and rows[0]["total"] == 3
    assert abs(rows[0]["mean_progression"] - 0.2) < 1e-9
    assert rows[1]["coverage"] == 1
    assert [r["rank"] for r in rows] == [1, 2]


def test_aggregate_board_scores_each_identity_on_its_own_objective_digest(tmp_path):
    # an atom on the same character but under a DIFFERENT objective must not leak in
    store = _new_store(tmp_path)
    ident = VAL_IDS[0]
    real = CATALOG[ident]
    other = _spec(name="other-objective", batch=((0, ident),))  # different digest, same character
    assert other.digest() != real.digest()
    atoms = [
        _atom(real, solution_digest="sha256:s", identity=ident, seed=0, progression=0.3),
        _atom(other, solution_digest="sha256:s", identity=ident, seed=1, progression=0.9),
    ]
    _seed(store, atoms, [real, other])

    rows = aggregate_board(store, [ident])

    assert len(rows) == 1
    assert rows[0]["coverage"] == 1
    assert abs(rows[0]["mean_progression"] - 0.3) < 1e-9  # 0.3 only, not (0.3+0.9)/2


def test_aggregate_board_empty_when_no_atoms(tmp_path):
    store = _new_store(tmp_path)
    assert aggregate_board(store, VAL_IDS) == []
