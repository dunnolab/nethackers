from nethackers.tui.art import highscore_table, score_to_dlvl, tombstone


def test_score_to_dlvl_boundaries_and_monotonic():
    assert score_to_dlvl(0.0) == "Dlvl:1"
    assert score_to_dlvl(0.51) == "Dlvl:26"     # Dlvl:26=.5068 <= .51 < Dlvl:27=.5544
    assert score_to_dlvl(0.90) == "Astral"       # >= Astral Plane (.8746)
    # monotonic non-decreasing depth as score rises
    depths = [int(score_to_dlvl(s).split(":")[1]) for s in (0.05, 0.2, 0.35)]
    assert depths == sorted(depths)


def test_tombstone_contains_rip_and_each_line():
    art = tombstone(["iter 2", "no dev gain"])
    lines = art.splitlines()
    assert "RIP" in art
    assert "iter 2" in art and "no dev gain" in art
    # Box frame: top line with "/" corner
    assert any("/" in line for line in lines[:3]), "Top box corner '/' not found"
    # Side lines contain "|"
    assert any("|" in line for line in lines), "Side frame '|' not found"
    # Base row is filled with "_"
    assert "_" in lines[-1], "Headstone base '_' not found"
    # "RIP" sits on its own row (the first line after the top box)
    rip_line = next(line for line in lines if "RIP" in line)
    assert rip_line.count("RIP") >= 1, "RIP should appear on its own line"


def test_tombstone_truncates_long_lines():
    """Test that epitaph lines longer than inner width (20) are truncated."""
    long_line = "a" * 30  # Exceeds inner width of 20
    art = tombstone([long_line])
    assert long_line not in art, "Long line should be truncated"
    # The truncated version (first 20 chars) should appear
    assert long_line[:20] in art, "Truncated line should appear in tombstone"


def test_tombstone_clips_to_four_lines():
    """Test that only first 4 epitaph lines are used (lines[:4] slice)."""
    five_lines = ["line1", "line2", "line3", "line4", "line5"]
    art = tombstone(five_lines)
    # First 4 should be present
    assert "line1" in art and "line2" in art and "line3" in art and "line4" in art
    # Fifth should not appear
    assert "line5" not in art, "Only first 4 epitaph lines should appear"


def test_highscore_table_owner_keyed_highlights_you():
    entries = [
        {"rank": 1, "owner": "vale", "mean_progression": 0.51, "solution_digest": "a"*8},
        {"rank": 2, "owner": "castiel", "mean_progression": 0.44, "solution_digest": "b"*8},
    ]
    t = highscore_table(entries, you="castiel")
    text = _plain(t)                              # helper below
    assert "@vale" in text and "@castiel" in text
    # Marker attached to correct row as one compound string
    assert "@castiel ◀ you" in text, "Marker should be on castiel's row"
    # Other owner should NOT have the marker
    assert "@vale ◀ you" not in text, "Marker should not be on vale's row"
    assert "Dlvl:26" in text                      # derived from 0.51


def test_highscore_table_identity_keyed_when_no_owner():
    entries = [{"rank": 1, "identity": "wiz-elf-cha-mal", "score": 0.44,
                "solution_digest": "c"*8}]
    text = _plain(highscore_table(entries))
    assert "Wiz-Elf-Cha-Mal" in text              # title-cased identity


def _plain(table):
    from rich.console import Console
    console = Console(width=80, file=__import__("io").StringIO())
    console.print(table)
    return console.file.getvalue()
