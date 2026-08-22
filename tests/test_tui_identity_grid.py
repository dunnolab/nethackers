"""Tests for the evolve-form objective picker (tui.identity_grid): the pure
nav/token/render helpers, and the IdentityGrid widget's key + selection
behavior."""
from __future__ import annotations

from rich.console import Console
from textual.app import App, ComposeResult

from nethackers.hub.objectives import IDENTITIES
from nethackers.tui.identity_grid import (
    IdentityGrid,
    nav_order,
    render_identity_grid,
    selection_token,
)

_WIZ = sorted(i for i in IDENTITIES if i.startswith("wiz-"))


# ---- pure helpers -------------------------------------------------------

def test_nav_order_random_then_roles_then_idents():
    order = nav_order()
    assert order[0] == "random"
    assert "role:wiz" in order
    assert order.index("role:wiz") < order.index("wiz-elf-cha-mal")
    assert len(order) == 1 + 13 + len(IDENTITIES)  # random + 13 role headers + 73 builds


def test_selection_token_cases():
    assert selection_token(set(), random_on=False) == ""
    assert selection_token(set(), random_on=True) == "random"
    assert selection_token({"a"}, random_on=True) == "random"  # random wins / exclusive
    assert selection_token(set(IDENTITIES), random_on=False) == "*"
    assert selection_token(set(_WIZ), random_on=False) == "wiz"  # exactly one full role
    two = {"wiz-elf-cha-mal", "val-dwa-law-fem"}
    assert selection_token(two, random_on=False) == "val-dwa-law-fem,wiz-elf-cha-mal"  # sorted list
    partial = set(_WIZ[:3])  # a partial role -> comma-list, not the bare role name
    assert selection_token(partial, random_on=False) == ",".join(sorted(partial))


def test_render_shows_boxes_counts_and_labels():
    sel = set(_WIZ) | {"val-dwa-law-fem"}
    out = Console(record=True, width=120)
    out.print(render_identity_grid(sel, "wiz-gno-neu-fem", random_on=False))
    text = out.export_text()
    assert "random" in text
    assert "Wizard" in text and "10/10" in text     # full role count
    assert "◼" in text and "◻" in text              # filled + empty boxes both present
    assert "elf-cha-mal" in text                     # a variation label
    assert "build(s) selected" in text


# ---- widget -------------------------------------------------------------

class _Host(App):
    def __init__(self) -> None:
        super().__init__()
        self.changes = 0

    def compose(self) -> ComposeResult:
        yield IdentityGrid(id="grid")

    def on_identity_grid_changed(self, _event: IdentityGrid.Changed) -> None:
        self.changes += 1


async def test_toggle_role_selects_all_its_builds():
    app = _Host()
    async with app.run_test() as pilot:
        grid = app.query_one(IdentityGrid)
        grid.cursor = "role:wiz"
        grid._toggle()
        await pilot.pause()
        assert grid.selected == set(_WIZ)
        assert grid.token() == "wiz"
        grid._toggle()  # toggling the full role again clears it
        assert grid.selected == set()


async def test_select_all_and_clear_all():
    app = _Host()
    async with app.run_test() as pilot:
        grid = app.query_one(IdentityGrid)
        grid.select_all()
        await pilot.pause()
        assert grid.selected == set(IDENTITIES) and grid.token() == "*"
        grid.clear_all()
        assert grid.selected == set() and grid.token() == ""


async def test_random_is_exclusive_with_identities():
    app = _Host()
    async with app.run_test() as pilot:
        grid = app.query_one(IdentityGrid)
        grid.cursor = "wiz-elf-cha-mal"
        grid._toggle()
        assert grid.selected == {"wiz-elf-cha-mal"} and not grid.random_on
        grid.cursor = "random"
        grid._toggle()
        await pilot.pause()
        assert grid.random_on and grid.selected == set() and grid.token() == "random"
        grid.cursor = "wiz-elf-cha-mal"
        grid._toggle()
        assert not grid.random_on and grid.selected == {"wiz-elf-cha-mal"}


async def test_space_key_toggles_cursor_cell_and_posts_changed():
    app = _Host()
    async with app.run_test() as pilot:
        grid = app.query_one(IdentityGrid)
        grid.focus()
        await pilot.pause()
        grid.cursor = "wiz-elf-cha-mal"
        await pilot.press("space")
        assert "wiz-elf-cha-mal" in grid.selected
        assert app.changes >= 1


async def test_arrow_down_moves_cursor():
    app = _Host()
    async with app.run_test() as pilot:
        grid = app.query_one(IdentityGrid)
        grid.focus()
        await pilot.pause()
        assert grid.cursor == "random"
        await pilot.press("down")
        assert grid.cursor == nav_order()[1]  # first role header
