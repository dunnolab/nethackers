from nethackers.tui.art import (
    NETHACKERS_BANNER,
    highscore_table,
    platform_status_line,
    score_to_dlvl,
    tombstone,
)

# --- NETHACKERS_BANNER: the 4-row figlet wordmark --------------------------


def test_nethackers_banner_is_a_nonempty_four_row_wordmark():
    lines = NETHACKERS_BANNER.splitlines()
    assert NETHACKERS_BANNER.strip()          # non-empty
    assert len(lines) == 4                     # figlet "small" font is 4 rows tall
    assert all(line.strip() for line in lines)  # no blank rows (stripped of stray newlines)


# --- platform_status_line: NetHack-idiom standing line ---------------------


def test_platform_status_line_renders_unreachable_programs_as_a_dash():
    line = platform_status_line(programs=None, runs=0, wins=0, tokens_display="0")
    assert "Programs:—" in line                # None -> "—", distinct from a real 0
    assert "Runs:0" in line and "Wins:0" in line
    assert "Evolved:0 tok" in line


def test_platform_status_line_renders_integer_counts():
    line = platform_status_line(programs=3, runs=12, wins=5, tokens_display="1.2M")
    assert "Programs:3" in line                # a real count, not the dash
    assert "Runs:12" in line
    assert "Wins:5" in line
    assert "Evolved:1.2M tok" in line          # the already-compacted token display


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
    # "RIP" sits on its own row ABOVE the epitaph (not merged)
    rip_idx = next(i for i, ln in enumerate(lines) if "RIP" in ln)
    epitaph_idx = next(i for i, ln in enumerate(lines) if "iter 2" in ln)
    assert rip_idx < epitaph_idx, "RIP row should appear above epitaph"
    assert "iter 2" not in lines[rip_idx], "RIP is NOT merged onto epitaph row"
    assert (
        lines[rip_idx].strip().strip("|").strip() == "RIP"
    ), "RIP should be alone on its row (only frame around it)"


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
