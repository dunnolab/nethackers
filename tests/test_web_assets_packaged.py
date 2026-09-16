"""Package data is not automatic here: `index.html` needs an explicit
force-include entry, so every sibling asset does too. Forgetting one ships a
wheel whose hub 500s on its first request, and the repo suite would not
notice -- it reads from src/ either way."""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB = REPO_ROOT / "src" / "nethackers" / "hub" / "web"


def test_every_web_asset_is_force_included():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = set(
        config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    )
    assets = {
        str(path.relative_to(REPO_ROOT))
        for path in WEB.iterdir()
        if path.is_file() and path.suffix != ".py"
    }
    assert assets <= included, f"missing force-include: {sorted(assets - included)}"
