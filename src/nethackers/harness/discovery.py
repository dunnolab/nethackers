"""Live model-availability discovery for the coding-agent operators.

Pure + dependency-injected (``run``/``http``/``home``/``which`` are all
overridable) so the default test suite exercises every branch with fakes and
never shells out to a real CLI or touches the network. ``None`` means "could
not determine" and every caller treats it as warn-and-proceed, never as a
block -- only a confident ``False`` from ``is_model_available`` blocks a run.

codex: the authoritative, version-filtered catalog is ``codex debug models``
(falling back to ``~/.codex/models_cache.json``). NEVER ``--bundled`` -- that
is the binary's shipped list and the backend rejects slugs absent from the
server catalog.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class ModelInfo:
    id: str                       # slug / model id passed to --model / -m
    label: str                    # display name for the picker
    reasoning: tuple[str, ...] = ()   # supported effort levels (may be empty)
    deprecated: bool = False


def list_models(
    backend: str,
    *,
    run: Callable = subprocess.run,
    http: Callable = httpx.get,
    home: Path | None = None,
) -> list[ModelInfo] | None:
    if backend == "codex":
        return _codex_models(run=run, home=home)
    return None


def _codex_parse(doc: object) -> list[ModelInfo] | None:
    raw = doc.get("models") if isinstance(doc, dict) else None
    if not isinstance(raw, list):
        return None
    out: list[ModelInfo] = []
    for m in raw:
        if not isinstance(m, dict) or m.get("visibility") == "hide":
            continue
        slug = m.get("slug")
        if not slug:
            continue
        levels = m.get("supported_reasoning_levels")
        out.append(ModelInfo(
            id=str(slug),
            label=str(m.get("display_name") or slug),
            reasoning=tuple(levels) if isinstance(levels, list) else (),
            deprecated=m.get("upgrade") is not None,
        ))
    return out


def _codex_models(*, run: Callable, home: Path | None) -> list[ModelInfo] | None:
    try:
        proc = run(["codex", "debug", "models"], capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and (proc.stdout or "").strip():
            parsed = _codex_parse(json.loads(proc.stdout))
            if parsed is not None:
                return parsed
    except Exception:
        pass
    cache = (home or Path.home()) / ".codex" / "models_cache.json"
    try:
        return _codex_parse(json.loads(cache.read_text()))
    except Exception:
        return None
