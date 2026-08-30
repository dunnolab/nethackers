"""Local, consented crash files: the zero-telemetry alternative to a
telemetry pipeline (reference class: uv/ruff/pip ship none). ``main()``'s
top-level exception guard (cli.py) writes one timestamped JSON file per
unexpected error under the active stage's ``data_root/crashes/``; nothing is
ever transmitted anywhere -- the file sits on disk until a user chooses to
open it (``nethackers report``) and paste/attach it somewhere themselves.

A leaf module: it may import ``config``/``diagnostics``, but MUST NOT import
``cli`` (cli.py imports FROM here -- the reverse would cycle).

``write_crash`` must NEVER raise. It runs at the exact moment ``main()`` is
already handling an exception; a crash reporter that itself crashes would
replace the user's ORIGINAL error with a worse, unrelated one right as
``main()`` is trying to explain what went wrong. Every step -- the
caller-supplied ``enrich()``, building the payload, creating the directory,
the atomic write -- is wrapped so any failure degrades gracefully: an
``enrich()`` failure alone only nulls out the ``doctor`` field (the file is
still written), while any OTHER failure (e.g. an unwritable data root) fails
the whole write and ``write_crash`` returns ``None`` -- there is no second
exception either way.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nethackers.config import load_stage
from nethackers.diagnostics import version_info

CRASH_DIRNAME = "crashes"
_REDACTED = "<redacted>"

# Flag NAMES (bare -- no leading dashes, no `=value`) whose value is always
# secret-shaped enough to redact outright, regardless of the shape heuristic
# below: `--token`/`--password`/etc are meant to carry exactly this kind of
# thing (e.g. `evolve --token <hub access token>`), so there's no reason to
# even look at the value's shape. Matched as a substring of the flag name
# (case-insensitively) so `--access-token`/`--client-secret`/`--api-key`
# style names are covered without enumerating every one by hand.
_SECRET_NEEDLES = ("token", "password", "passwd", "secret", "key")

# A conservative shape heuristic for a secret pasted where no recognized
# flag name caught it (an unanticipated flag, or a bare positional): long
# (>=20 chars -- well past any objective/model/scope id this CLI uses), no
# `/` (rules out paths and URLs, both common here and NOT secret), and a mix
# of letters + digits (true of a GitHub PAT / JWT / API key; false of this
# CLI's own short, hyphenated, rarely-mixed-case ids like "val-dwa-law-fem"
# or "claude-opus-5"). A heuristic, not a guarantee -- belt, not the only
# layer; the flag-name check above is the primary defense for anything
# already named `--token`/`--password`/etc.
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_.\-]{20,}$")


def _flag_name(item: str) -> str | None:
    """``--foo`` -> ``"foo"``; ``--foo=bar`` -> ``"foo"``; anything else
    (a positional, a short ``-o``) -> ``None``."""
    if not item.startswith("--"):
        return None
    return item[2:].split("=", 1)[0] or None


def _looks_secret_shaped(value: str) -> bool:
    if "/" in value or _OPAQUE_RE.match(value) is None:
        return False
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def _sanitize_argv(argv: list[str]) -> list[str]:
    """Redact anything secret-shaped to ``"<redacted>"``, keeping every
    subcommand and flag NAME intact -- a crash file that can't even show
    *which* command failed is useless, but one that carries a hub access
    token because it happened to be on the command line is worse than
    useless."""
    out: list[str] = []
    redact_next = False
    for item in argv:
        if redact_next:
            out.append(_REDACTED)
            redact_next = False
            continue
        name = _flag_name(item)
        if name is not None and any(n in name.lower() for n in _SECRET_NEEDLES):
            if "=" in item:
                flag = item.split("=", 1)[0]
                out.append(f"{flag}={_REDACTED}")
            else:
                out.append(item)  # keep the flag NAME; its value is the next item
                redact_next = True
            continue
        out.append(_REDACTED if _looks_secret_shaped(item) else item)
    return out


def _crash_dir() -> Path:
    return load_stage().data_root / CRASH_DIRNAME


def write_crash(
    exc: BaseException,
    *,
    argv: list[str],
    enrich: Callable[[], dict[str, Any]] | None = None,
) -> Path | None:
    """Write one timestamped crash file under ``load_stage().data_root/
    crashes/`` and return its path -- or ``None`` on ANY failure (this
    function never raises; see the module docstring). ``enrich`` -- e.g. a
    full ``diagnostics`` doctor snapshot -- is separately wrapped: its own
    failure only nulls the ``doctor`` field, it never fails the write."""
    try:
        doctor: dict[str, Any] | None
        try:
            doctor = enrich() if enrich is not None else None
        except Exception:
            doctor = None

        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "nethackers_version": version_info()["nethackers"],
            "python": sys.version,
            "platform": platform.platform(),
            "argv": _sanitize_argv(list(argv)),
            "exc_type": type(exc).__name__,
            "traceback": "".join(traceback.format_exception(exc)),
            "doctor": doctor,
        }

        crash_dir = _crash_dir()
        crash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = crash_dir / f"{stamp}-{uuid.uuid4().hex[:8]}.json"
        tmp = crash_dir / f".tmp-{uuid.uuid4().hex}"
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, dest)  # atomic within crash_dir (same filesystem)
        return dest
    except Exception:
        return None


def latest() -> Path | None:
    """The newest crash file by mtime, or ``None`` when there are none yet
    (including when the crash dir itself doesn't exist)."""
    crash_dir = _crash_dir()
    if not crash_dir.is_dir():
        return None
    files = [p for p in crash_dir.glob("*.json") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def load(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(path.read_text())
    return result
