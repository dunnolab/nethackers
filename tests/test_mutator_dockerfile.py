"""Dockerfile.mutator installs every agent CLI at an exact version, so two builds
of the same files ship the same agents and a bump is a visible edit that moves
the mutator's fingerprint."""
from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile.mutator"


def _npm_global_packages() -> list[str]:
    text = DOCKERFILE.read_text().replace("\\\n", " ")
    match = re.search(r"npm install -g ([^&\n]+)", text)
    assert match, "Dockerfile.mutator no longer installs npm packages globally"
    return match.group(1).split()


def test_every_agent_cli_is_pinned_to_an_exact_version():
    packages = _npm_global_packages()
    assert {p.rsplit("@", 1)[0] for p in packages} == {
        "@anthropic-ai/claude-code", "@openai/codex", "opencode-ai"}
    for package in packages:
        name, _, version = package.rpartition("@")
        # An exact version starts with a digit; a dist-tag (beta, latest) doesn't.
        assert name and re.match(r"\d", version), f"{package} is not pinned to a version"
