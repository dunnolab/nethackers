"""Output-format dispatch for the M2a hub-facing CLI subcommands (CLI-UX
pass, folding in ``rich``): one ``emit(data, fmt, *, table, plain)``
function decides, from ``-o/--output`` (falling back to
``$NETHACKERS_OUTPUT``, falling back to ``"auto"``), whether a read
subcommand's response becomes a beautified ``rich`` render, the baseline
pure-Python "plain" table (``nethackers.hubclient.client``'s
``render_*``/``_table``), or raw JSON.

JSON is always ``json.dumps`` of the EXACT raw hub response, never
``rich.print_json`` (which emits ANSI/syntax-highlighting escape codes that
break ``jq``) and never a stringified table (which would silently narrow
e.g. a full 64-character digest down to whatever a table column decided to
show) -- so piping to ``jq`` or feeding a coding agent always gets the
complete, unmodified data.

Two module-level ``Console``\\ s split human chrome from machine-readable
data, both auto-detecting TTY/color/width from whatever ``sys.stdout``/
``sys.stderr`` currently are (resolved lazily on every write, not frozen at
import time -- see ``rich.console.Console.file``, a property -- so this
plays correctly with ``pytest``'s ``capsys``, which swaps those streams out
per-test):

- ``console`` (stdout): where ``emit``'s ``table``/``json``/``plain``
  renders all go.
- ``err`` (stderr): where every bit of human chrome that ISN'T a data
  render belongs -- the ``register`` device-flow's "visit this URL" prompt,
  error messages (e.g. an unknown objective), any future progress output.
  ``cli.py`` routes all of that through ``err`` so stdout stays
  machine-clean no matter what -- in particular, ``-o json`` mode's stdout
  carries the JSON payload and *nothing else*.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from rich.console import Console, RenderableType

console = Console()  # stdout: auto-detects TTY/color/width per write
err = Console(stderr=True)  # stderr: ALL human chrome (prompts, errors, progress)


def resolve(fmt: str | None) -> str:
    """Resolve the effective output format, three tiers of precedence:

    1. An explicit non-``"auto"`` ``fmt`` (``-o table``/``-o json``/
       ``-o plain``, whether given before or after the subcommand) always
       wins outright -- neither ``$NETHACKERS_OUTPUT`` nor TTY-detection is
       even consulted.
    2. Otherwise (``fmt`` is ``None``/``""``/``"auto"`` -- which is also
       the CLI's own top-level default, so this is the common case of "the
       user didn't ask for anything in particular"): ``$NETHACKERS_OUTPUT``
       wins if set to a concrete, non-``"auto"`` value.
    3. Otherwise: TTY-detection -- ``"table"`` when stdout is a real
       terminal, else ``"json"`` -- so a human at a shell gets the
       beautiful render by default, while a pipe, a redirect, or a coding
       agent gets clean JSON by default, with no flag required either way.

    (A literal ``fmt="auto"`` is deliberately treated the same as
    "unset" for tiers 2/3 -- it's the CLI's default value, not a distinct
    fourth choice, so it must still defer to ``$NETHACKERS_OUTPUT``/TTY-
    detection exactly like never passing ``-o`` at all; only genuinely
    concrete choices short-circuit tier 1.)"""
    resolved = fmt or "auto"
    if resolved == "auto":
        resolved = os.environ.get("NETHACKERS_OUTPUT") or "auto"
    if resolved == "auto":
        return "table" if console.is_terminal else "json"
    return resolved


def emit(
    data: Any,
    fmt: str | None = "auto",
    *,
    table: Callable[[Any], RenderableType] | None = None,
    plain: Callable[[Any], str] | None = None,
) -> None:
    """Print ``data`` per the resolved format (see ``resolve``):

    - ``"table"`` (and a ``table`` renderer was given): ``table(data)``
      rendered through the shared ``console`` -- a ``rich`` renderable.
    - ``"plain"`` (and a ``plain`` renderer was given): ``plain(data)``
      (the baseline pure-Python aligned render) via plain ``print``.
    - ``"json"``, or a resolved format this call site has no renderer for:
      ``json.dumps(data, indent=2)`` on stdout -- the safe fallback, and
      always the exact raw data, never routed through either renderer.
    """
    resolved = resolve(fmt)
    if resolved == "table" and table is not None:
        console.print(table(data))
    elif resolved == "plain" and plain is not None:
        print(plain(data))
    else:
        print(json.dumps(data, indent=2))
