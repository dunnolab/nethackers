"""The automatic mutator workflow must start for a change to any mutator input:
its path filters have to cover the same list the fingerprint hashes."""
from __future__ import annotations

from pathlib import Path

from nethackers import image_inputs

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "mutator-image.yml"


def test_path_filters_cover_every_mutator_input_for_prs_and_main():
    text = WORKFLOW.read_text()
    for rel in image_inputs.MUTATOR_INPUT_PATHS:
        pattern = f"{rel}/**" if (REPO / rel).is_dir() else rel
        assert text.count(f"- {pattern}\n") == 2, pattern      # pull_request and push
    for extra in ("src/nethackers/_image_pins.py", "src/nethackers/image_inputs.py"):
        assert text.count(f"- {extra}\n") == 2, extra
