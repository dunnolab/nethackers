import io

from rich.console import Console

from nethackers.hubclient.render import render_board

ENTRIES = [
    {"rank": 1, "solution_digest": "a"*12, "owner": "vale",
     "ascensions": 0, "median_progression": 0.5, "mean_progression": 0.51},
    {"rank": 2, "solution_digest": "b"*12, "owner": "castiel",
     "ascensions": 0, "median_progression": 0.4, "mean_progression": 0.44},
]

def _plain(r):
    c = Console(width=100, file=io.StringIO())
    c.print(r)
    return c.file.getvalue()

def test_you_row_is_emphasized():
    out = _plain(render_board(ENTRIES, you="castiel"))
    assert "castiel" in out
    # default (no `you`) is unchanged / does not raise
    assert "castiel" in _plain(render_board(ENTRIES))
