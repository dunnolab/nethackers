from nethackers.tui.art import score_to_dlvl, tombstone, highscore_table


def test_score_to_dlvl_boundaries_and_monotonic():
    assert score_to_dlvl(0.0) == "Dlvl:1"
    assert score_to_dlvl(0.51) == "Dlvl:26"     # Dlvl:26=.5068 <= .51 < Dlvl:27=.5544
    assert score_to_dlvl(0.90) == "Astral"       # >= Astral Plane (.8746)
    # monotonic non-decreasing depth as score rises
    depths = [int(score_to_dlvl(s).split(":")[1]) for s in (0.05, 0.2, 0.35)]
    assert depths == sorted(depths)


def test_tombstone_contains_rip_and_each_line():
    art = tombstone(["iter 2", "no dev gain"])
    assert "RIP" in art
    assert "iter 2" in art and "no dev gain" in art
    assert "_" in art.splitlines()[-1]           # headstone base


def test_highscore_table_owner_keyed_highlights_you():
    entries = [
        {"rank": 1, "owner": "vale", "mean_progression": 0.51, "solution_digest": "a"*8},
        {"rank": 2, "owner": "castiel", "mean_progression": 0.44, "solution_digest": "b"*8},
    ]
    t = highscore_table(entries, you="castiel")
    text = _plain(t)                              # helper below
    assert "@vale" in text and "@castiel" in text
    assert "◀ you" in text                        # the you-row is marked
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
