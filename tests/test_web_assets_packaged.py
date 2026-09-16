"""Package data is not automatic here: `index.html` needs an explicit
force-include entry, so every sibling asset does too. Forgetting one ships a
wheel whose hub 500s on its first request, and the repo suite would not
notice -- it reads from src/ either way."""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
WEB = SRC / "nethackers" / "hub" / "web"


def test_every_web_asset_is_force_included():
    """I6 says "under" web/, not "directly inside" it, so a nested asset
    (e.g. a future web/assets/logo.svg) must be covered too -- hence
    ``rglob("*")``, not ``iterdir()``. And a present key with the wrong wheel
    destination (a typo like ``nethackers/hub/web/breif.md``) is exactly as
    broken as a missing one, so each source path's destination is checked
    against where it actually needs to land, not just checked for presence
    as a key."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    # {source path relative to the repo -> its correct wheel destination}
    expected = {
        str(path.relative_to(REPO_ROOT)): str(path.relative_to(SRC))
        for path in WEB.rglob("*")
        if path.is_file() and path.suffix != ".py"
    }

    missing = sorted(source for source in expected if source not in included)
    assert not missing, f"missing force-include: {missing}"

    wrong_destination = sorted(
        (source, included[source], destination)
        for source, destination in expected.items()
        if included[source] != destination
    )
    assert not wrong_destination, (
        "force-include (source, actual destination, expected destination): "
        f"{wrong_destination}"
    )
