from rich.console import Console

from nethackers.tui.screens.home import causes_panel


def _render(table) -> str:
    console = Console(width=60, record=True)
    console.print(table)
    return console.export_text()


def test_causes_panel_lists_causes_by_count_desc():
    out = _render(causes_panel({"killed by a jackal": 3, "starved to death": 7}))
    assert "killed by a jackal" in out and "starved to death" in out
    # most frequent first
    assert out.index("starved to death") < out.index("killed by a jackal")


def test_causes_panel_caps_to_eight_rows():
    many = {f"death-{i:02d}": i for i in range(1, 20)}  # death-01..death-19, counts 1..19
    out = _render(causes_panel(many))
    assert "death-19" in out       # highest count shown
    assert "death-11" not in out   # 9th-highest and below trimmed (top 8 == counts 19..12)
