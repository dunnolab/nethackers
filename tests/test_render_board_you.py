import io

from rich.console import Console

from nethackers.hubclient.render import render_board

ENTRIES = [
    {"rank": 1, "solution_digest": "a"*12, "owner": "vale",
     "ascensions": 0, "median_progression": 0.5, "mean_progression": 0.51},
    {"rank": 2, "solution_digest": "b"*12, "owner": "castiel",
     "ascensions": 0, "median_progression": 0.4, "mean_progression": 0.44},
]

def _plain(r, width: int = 120):
    c = Console(width=width, file=io.StringIO())
    c.print(r)
    return c.file.getvalue()

def test_you_row_is_emphasized():
    # With you="castiel": castiel's row should have the ◀ you marker, vale's should not
    out = _plain(render_board(ENTRIES, you="castiel"))
    lines = out.split('\n')

    castiel_line = None
    vale_line = None
    for line in lines:
        if "castiel" in line and "@ castiel" in line:
            castiel_line = line
        if "vale" in line and "@ vale" in line:
            vale_line = line

    assert castiel_line is not None, "Could not find castiel row in output"
    assert vale_line is not None, "Could not find vale row in output"
    assert "◀ you" in castiel_line, f"castiel row should have ◀ you marker: {castiel_line}"
    assert "◀ you" not in vale_line, f"vale row should not have ◀ you marker: {vale_line}"

    # With you=None: no emphasis marker at all
    out_no_you = _plain(render_board(ENTRIES))
    assert "◀ you" not in out_no_you, "Default (you=None) should not have ◀ you marker"
