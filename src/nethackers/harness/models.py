"""Curated model + effort options per coding harness, for the TUI's Model /
Effort pickers (the CLI + operators accept any string; a "Custom…" entry covers
anything not listed here). Sourced from the vendors' own docs:

- claude: https://code.claude.com/docs/en/model-config +
  https://platform.claude.com/docs/en/about-claude/models/overview
  (dateless IDs are pinned snapshots from the 4.6 generation on).
- codex:  https://learn.chatgpt.com/docs/models

Both harnesses take a reasoning-effort level -- claude via ``--effort``,
codex via ``-c model_reasoning_effort=`` -- over the same names (codex also
has ``ultra``; reach it via Custom). Keep this the single edit point when a
vendor ships a new model.
"""
from __future__ import annotations

# label shown in the dropdown -> the exact string passed to --model / -m
MODELS: dict[str, list[tuple[str, str]]] = {
    "claude": [
        ("Fable 5", "claude-fable-5"),
        ("Opus 5", "claude-opus-5"),
        ("Opus 4.8", "claude-opus-4-8"),
        ("Sonnet 5", "claude-sonnet-5"),
        ("Haiku 4.5", "claude-haiku-4-5"),
    ],
    "codex": [
        ("gpt-5.6-sol", "gpt-5.6-sol"),
        ("gpt-5.6-terra", "gpt-5.6-terra"),
        ("gpt-5.6-luna", "gpt-5.6-luna"),
        ("gpt-5.3-codex-spark", "gpt-5.3-codex-spark"),
        ("gpt-5.5", "gpt-5.5"),
    ],
}

# shared across both harnesses (low..max). codex-only "ultra" / claude-only
# "ultracode" are reachable via a Custom effort string if ever needed.
EFFORTS: list[str] = ["low", "medium", "high", "xhigh", "max"]


def models_for(backend: str) -> list[tuple[str, str]]:
    return MODELS.get(backend, [])
