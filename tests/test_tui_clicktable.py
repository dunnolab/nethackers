from textual.app import App, ComposeResult
from textual.coordinate import Coordinate

from nethackers.tui.screens._clicktable import ClickTable


class _Host(App):
    def __init__(self) -> None:
        super().__init__()
        self.selected: list[Coordinate] = []

    def compose(self) -> ComposeResult:
        t = ClickTable(id="t", cursor_type="cell")
        t._valid_fn = lambda row, col: True
        yield t

    def on_mount(self) -> None:
        t = self.query_one("#t", ClickTable)
        t.add_columns("a", "b")
        t.add_row("1", "2")
        t.add_row("3", "4")

    def on_data_table_cell_selected(self, event: ClickTable.CellSelected) -> None:
        self.selected.append(event.coordinate)


async def test_single_click_selects_that_cell():
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        table = host.query_one("#t", ClickTable)
        table.move_cursor(row=0, column=0)  # start away from the cell we're about to click
        await pilot.click("#t", offset=(3, 2))  # a body cell -> (row=1, column=1)
        await pilot.pause()
        # single click landed the cursor exactly on the clicked cell and selected it
        # (one physical click; Textual's own DataTable._on_click also fires -- see
        # test-mechanics note in the task report -- so we assert "selected, and only
        # ever the clicked cell" rather than an exact message count)
        assert table.cursor_coordinate == Coordinate(1, 1)
        assert host.selected
        assert set(host.selected) == {Coordinate(1, 1)}


async def test_hover_moves_cursor_only_onto_valid_cells():
    host = _Host()
    async with host.run_test() as pilot:
        await pilot.pause()
        t = host.query_one("#t", ClickTable)
        t._valid_fn = lambda row, col: col == 1        # only 2nd column is clickable
        t.move_cursor(row=0, column=1)

        # simulate a mouse-move meta onto an INVALID cell (col 0)
        class _Style:  # minimal stand-in carrying .meta
            meta = {"row": 1, "column": 0}

        t._on_mouse_move(type("E", (), {"style": _Style()})())
        assert t.cursor_coordinate == Coordinate(0, 1)  # unchanged -> stayed on valid cell
