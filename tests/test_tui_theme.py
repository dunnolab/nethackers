from nethackers.tui import theme


def test_glyphs_cover_statuses():
    assert theme.glyph("ascended") == "★"
    assert theme.glyph("died") == "☠"
    assert theme.glyph("running") == "@"
    assert theme.glyph("totally-unknown") == "·"   # safe fallback


def test_styles_delegate_to_live_maps():
    assert theme.status_style("ascended") == "bold green"
    assert theme.status_style("nope") == ""            # unknown -> empty
    assert theme.progress_style(0.9) == "bold green"
    assert theme.progress_style(0.0) == "dim"


def test_palette_and_css_present():
    assert {"hp", "gold", "green"} <= set(theme.PALETTE)
    assert all(v.startswith("#") for v in theme.PALETTE.values())
    assert ".tabbar" in theme.CSS and ".panel" in theme.CSS
    assert "dock: top" in theme.CSS  # the tabbar band docks to the top
