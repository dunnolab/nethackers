"""Curated model + effort options per coding harness, for the TUI's Model /
Effort pickers (the CLI + operators accept any string; a "Custom…" entry covers
anything not listed here). Sourced from the vendors' own docs:

- claude: https://code.claude.com/docs/en/model-config +
  https://platform.claude.com/docs/en/about-claude/models/overview
  (dateless IDs are pinned snapshots from the 4.6 generation on).
- codex:  https://learn.chatgpt.com/docs/models

All three harnesses take a reasoning-effort level: Claude via ``--effort``,
Codex via ``-c model_reasoning_effort=``, and OpenCode 2 as the model variant
suffix. Keep this the single edit point when a vendor ships a new model.
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
    # OpenCode 2 is provider-agnostic and discovers provider/model pairs live via
    # `opencode2 models`; a static catalogue would become stale immediately.
    "opencode2": [],
}

# shared across all harnesses (low..max). codex-only "ultra" / claude-only
# "ultracode" are reachable via a Custom effort string if ever needed.
EFFORTS: list[str] = ["low", "medium", "high", "xhigh", "max"]


def models_for(backend: str) -> list[tuple[str, str]]:
    return MODELS.get(backend, [])
