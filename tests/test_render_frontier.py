"""Tests for the shared role x variation frontier grid: the rich
``render_frontier_grid`` (``nethackers.hubclient.render``) and its plain-text
baseline ``plain_frontier`` (``nethackers.hubclient.client``) -- both pure
formatters over a ``{identity: float | None}`` map, no hub calls. Assertions
are on substrings of the rendered text (structure), never a full-frame
snapshot -- see each renderer's own docstring for the exact contract."""

from __future__ import annotations

from rich.console import Console

from nethackers.hubclient.client import plain_frontier
from nethackers.hubclient.render import ROLE_FULL, ROLE_ORDER, render_frontier_grid


def _render(renderable, width: int = 120) -> str:
    """``renderable`` rendered to plain text at a fixed width, via
    ``Console.capture()`` per the brief -- no ``file=``/``force_terminal``
    override needed since an un-forced ``Console`` in a non-tty test
    process already emits plain (non-ANSI) text."""
    console = Console(width=width)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


# --- render_frontier_grid (rich) --------------------------------------------


def test_render_frontier_grid_shows_role_and_variation_detail():
    scores = {"wiz-elf-cha-mal": 0.61, "wiz-elf-cha-fem": 0.10}
    out = _render(render_frontier_grid(scores))

    assert "Archeologist" in out  # every role's full name appears, evaluated or not
    assert "Wizard" in out
    assert "elf-cha-mal" in out  # variation label = identity minus role prefix
    assert "0.61" in out

    expected_mean = sum(scores.values()) / len(scores)  # computed, not hardcoded
    assert f"{expected_mean:.2f}" == "0.35"  # pin against the brief's own worked example
    assert f"mean {expected_mean:.2f}" in out

    assert "—" in out  # unevaluated variations (e.g. any non-wiz identity) render as a dash


def test_render_frontier_grid_empty_returns_without_error_and_shows_every_role():
    out = _render(render_frontier_grid({}))

    assert len(ROLE_FULL) == 13
    assert set(ROLE_ORDER) == set(ROLE_FULL)
    for full_name in ROLE_FULL.values():
        assert out.count(full_name) == 1  # each role appears exactly once, none dropped


def test_render_frontier_grid_note_renders_above_the_grid():
    out = _render(render_frontier_grid({}, note="frozen -- parent gen 4"))
    assert "frozen -- parent gen 4" in out
    assert "Wizard" in out  # the grid itself still renders alongside the note


def test_render_frontier_grid_shows_autoascend_floor_and_program_delta():
    out = _render(render_frontier_grid(
        {"wiz-elf-cha-mal": 0.61},
        baseline_scores={"wiz-elf-cha-mal": 0.50, "wiz-elf-cha-fem": 0.20},
        baseline_floor=True,
    ))

    assert "61.0% +11.0%" in out
    assert "20.0%  aa" in out


def test_render_frontier_grid_preserves_small_contest_site_deltas():
    out = _render(render_frontier_grid(
        {
            "mon-hum-cha-fem": 0.134, "mon-hum-cha-mal": 0.134,
            "mon-hum-law-fem": 0.100, "mon-hum-law-mal": 0.109,
            "mon-hum-neu-fem": 0.104, "mon-hum-neu-mal": 0.104,
        },
        baseline_scores={
            "mon-hum-cha-fem": 0.133, "mon-hum-cha-mal": 0.133,
            "mon-hum-law-fem": 0.093, "mon-hum-law-mal": 0.099,
            "mon-hum-neu-fem": 0.104, "mon-hum-neu-mal": 0.099,
        },
        baseline_floor=True,
    ))

    assert "Monk   11.4% +0.4%" in out
    assert "hum-cha-fem  13.4% +0.1%" in out
    assert "hum-neu-fem  10.4%  aa" in out


# --- plain_frontier (pure Python baseline) ----------------------------------


def test_plain_frontier_contains_identities_and_values():
    scores = {"wiz-elf-cha-mal": 0.61, "wiz-elf-cha-fem": 0.10}
    out = plain_frontier(scores)

    assert "wiz-elf-cha-mal" in out
    assert "0.61" in out
    assert "wiz-elf-cha-fem" in out
    assert "0.10" in out


def test_plain_frontier_marks_none_values_with_a_dash():
    out = plain_frontier({"wiz-elf-cha-mal": 0.61, "wiz-elf-cha-fem": None})
    assert "wiz-elf-cha-mal" in out and "0.61" in out
    assert "wiz-elf-cha-fem" in out
    assert "—" in out


def test_plain_frontier_empty_is_a_friendly_one_liner():
    out = plain_frontier({})
    assert out == "no frontier data yet."


def test_plain_frontier_all_none_is_also_a_friendly_one_liner():
    # Every value None (nothing evaluated yet) reads the same as "no data"
    # -- distinguishing "key present but None" from "key absent" isn't
    # meaningful to a human reading the CLI output.
    out = plain_frontier({"wiz-elf-cha-mal": None, "wiz-elf-cha-fem": None})
    assert out == "no frontier data yet."
