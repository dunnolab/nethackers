"""Tests for ``nethackers.hub.views.attainment``: the append-only,
monotonic attainment view (M2a Task 7). See task-7-context.md -- a cell
(identity, label) is lit iff some atom on identity has progression >=
ACHIEVEMENTS[label]. Writes only ever INSERT or ratchet-to-earliest (never
DELETE anywhere in the module), so a lit cell can never go dark and
``first_*`` only ever moves earlier, never later.
"""

from __future__ import annotations

from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.contracts.models import Atom
from nethackers.hub.store import Store
from nethackers.hub.views.attainment import read_attainment, update_attainment

IDENTITY = "val-dwa-law-fem"
OTHER_IDENTITY = "wiz-elf-cha-fem"


def _atom(**overrides):
    fields = dict(
        solution_digest="sha256:solution-a",
        owner="sam",
        tier="self-reported",
        identity=IDENTITY,
        seed=0,
        progression=ACHIEVEMENTS["Dlvl:3"],
        milestone="Dlvl:3",
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


def _cell(cells, identity, milestone):
    return next(
        (c for c in cells if c["identity"] == identity and c["milestone"] == milestone), None
    )


def test_monotonic_a_later_weaker_atom_never_unlights_a_cell(tmp_path):
    # Property 1: a strong atom at T1 lights up through Dlvl:10; a later,
    # weaker atom at T2 (>T1) that only reaches Dlvl:3 must not remove the
    # deeper cell the first atom lit, and must not touch its first_* --
    # there is no DELETE anywhere in the write path, so a previously-lit
    # cell can never go dark.
    store = _new_store(tmp_path)
    strong = _atom(
        solution_digest="sha256:strong",
        owner="strong-owner",
        progression=ACHIEVEMENTS["Dlvl:10"],
        milestone="Dlvl:10",
    )
    update_attainment(store, [strong], now="2026-01-01T00:00:00Z")

    weak = _atom(
        solution_digest="sha256:weak",
        owner="weak-owner",
        progression=ACHIEVEMENTS["Dlvl:3"],
        milestone="Dlvl:3",
    )
    update_attainment(store, [weak], now="2026-01-02T00:00:00Z")

    cells = read_attainment(store, identity=IDENTITY)
    deep = _cell(cells, IDENTITY, "Dlvl:10")
    assert deep is not None
    assert deep["first_owner"] == "strong-owner"
    assert deep["first_at"] == "2026-01-01T00:00:00Z"
    assert deep["holder_count"] == 1  # the later weak atom never reached Dlvl:10


def test_first_is_earliest_by_timestamp_and_later_holders_never_displace_it(tmp_path):
    # Property 2: process a later-now solution first, then an earlier-now
    # solution -- first_* must end up as the earlier one regardless of call
    # order. A still-later third solution must not displace it either.
    # holder_count accumulates every distinct solution that reached the cell.
    store = _new_store(tmp_path)
    milestone = "Dlvl:5"
    progression = ACHIEVEMENTS[milestone]

    late = _atom(
        solution_digest="sha256:late", owner="late-owner",
        progression=progression, milestone=milestone,
    )
    update_attainment(store, [late], now="2026-01-02T00:00:00Z")

    early = _atom(
        solution_digest="sha256:early", owner="early-owner",
        progression=progression, milestone=milestone,
    )
    update_attainment(store, [early], now="2026-01-01T00:00:00Z")

    cells = read_attainment(store, identity=IDENTITY)
    cell = _cell(cells, IDENTITY, milestone)
    assert cell is not None
    assert cell["first_solution"] == "sha256:early"
    assert cell["first_owner"] == "early-owner"
    assert cell["first_at"] == "2026-01-01T00:00:00Z"
    assert cell["holder_count"] == 2

    latest = _atom(
        solution_digest="sha256:latest", owner="latest-owner",
        progression=progression, milestone=milestone,
    )
    update_attainment(store, [latest], now="2026-01-03T00:00:00Z")

    cells = read_attainment(store, identity=IDENTITY)
    cell = _cell(cells, IDENTITY, milestone)
    assert cell is not None
    assert cell["first_solution"] == "sha256:early"  # unchanged: later never displaces
    assert cell["first_owner"] == "early-owner"
    assert cell["first_at"] == "2026-01-01T00:00:00Z"
    assert cell["holder_count"] == 3


def test_read_attainment_identity_filter(tmp_path):
    # Property 3.
    store = _new_store(tmp_path)
    a = _atom(identity=IDENTITY, solution_digest="sha256:a")
    b = _atom(identity=OTHER_IDENTITY, solution_digest="sha256:b")
    update_attainment(store, [a, b], now="2026-01-01T00:00:00Z")

    filtered = read_attainment(store, identity=IDENTITY)
    assert len(filtered) > 0
    assert all(c["identity"] == IDENTITY for c in filtered)

    unfiltered = read_attainment(store)
    assert {c["identity"] for c in unfiltered} == {IDENTITY, OTHER_IDENTITY}


def test_reaching_a_deep_milestone_lights_shallower_ones_but_not_deeper_ones(tmp_path):
    # Property 4: concrete ACHIEVEMENTS values -- Dlvl:20 = 0.37895667923256743,
    # Dlvl:15 = 0.30881240110986263 (shallower: must light), Dlvl:25 =
    # 0.46637631565303056 (deeper: 0.466 > 0.379, must NOT light).
    store = _new_store(tmp_path)
    assert ACHIEVEMENTS["Dlvl:25"] > ACHIEVEMENTS["Dlvl:20"] > ACHIEVEMENTS["Dlvl:15"]

    atom = _atom(progression=ACHIEVEMENTS["Dlvl:20"], milestone="Dlvl:20")
    update_attainment(store, [atom], now="2026-01-01T00:00:00Z")

    cells = read_attainment(store, identity=IDENTITY)
    lit = {c["milestone"] for c in cells}
    assert "Dlvl:20" in lit
    assert "Dlvl:15" in lit
    assert "Dlvl:1" in lit
    assert "Dlvl:25" not in lit


def test_holder_dedup_counts_one_solution_once_even_across_eight_seeds(tmp_path):
    # Property 5: 8 atoms of the SAME solution, different seeds ->
    # holder_count == 1 for the lit cells (PK dedups on
    # (identity, milestone, solution_digest)), not 8.
    store = _new_store(tmp_path)
    milestone = "Dlvl:5"
    atoms = [
        _atom(
            solution_digest="sha256:one-solution", owner="sam", seed=seed,
            progression=ACHIEVEMENTS[milestone], milestone=milestone,
        )
        for seed in range(8)
    ]
    update_attainment(store, atoms, now="2026-01-01T00:00:00Z")

    cells = read_attainment(store, identity=IDENTITY)
    for label in ("Dlvl:1", milestone):
        cell = _cell(cells, IDENTITY, label)
        assert cell is not None
        assert cell["holder_count"] == 1


def test_atom_with_no_milestone_lights_nothing(tmp_path):
    # Property 6: milestone=None is skipped outright, regardless of how
    # high progression is.
    store = _new_store(tmp_path)
    atom = _atom(progression=0.9, milestone=None)
    update_attainment(store, [atom], now="2026-01-01T00:00:00Z")

    assert read_attainment(store, identity=IDENTITY) == []


def test_update_attainment_is_idempotent(tmp_path):
    # Property 7: calling update_attainment twice with the same atoms+now
    # leaves the same rows -- no duplicate holders, first_* unchanged.
    store = _new_store(tmp_path)
    atoms = [
        _atom(solution_digest="sha256:a", owner="a-owner", seed=0),
        _atom(solution_digest="sha256:b", owner="b-owner", seed=1),
    ]
    now = "2026-01-01T00:00:00Z"

    update_attainment(store, atoms, now=now)
    first_pass = read_attainment(store, identity=IDENTITY)
    holder_rows_first = store.conn.execute("SELECT COUNT(*) FROM attainment_holders").fetchone()[
        0
    ]

    update_attainment(store, atoms, now=now)
    second_pass = read_attainment(store, identity=IDENTITY)
    holder_rows_second = store.conn.execute(
        "SELECT COUNT(*) FROM attainment_holders"
    ).fetchone()[0]

    assert first_pass == second_pass
    assert holder_rows_first == holder_rows_second
