"""The typed pull-progress event seam (spec S5.5): a small, LEAF vocabulary
for reporting sandbox-image acquisition progress, replacing the untyped
``on_line: Callable[[str], None]`` callback (raw ``docker pull`` stdout
text) that ``sandbox_preflight.py`` used to be the only way to observe a
pull. Both the CLI (``cli.py``) and, later, the TUI's provisioning screen
consume the same ``PullEvent`` stream from the same emitter
(``sandbox_preflight._pull_image``/``ensure_image``) -- one source of truth
for "how far along is this pull", rendered two different ways.

Progress is counted in layers AND, when docker prints them, bytes. A pull
runs in a pseudo-terminal (``sandbox_preflight._pull_image``), so docker
prints its per-layer byte progress (``Downloading [==>  ]  45.2MB/355MB``);
``parse_pull_line`` keeps each layer's current/total and every event carries
the sums. That reverses an earlier ruling ("layers, never bytes"): with one
355 MB layer in an 875 MB pull, a layer count sat still for most of the
download and looked stuck (spec 2026-09-22 D8). Bytes are ``None`` whenever
no byte line has been seen -- Podman's output, or a pull through a pipe --
and consumers fall back to the layer count.

Kept a leaf module: no docker, no TUI/CLI import. Rendering the ref through
``diagnostics._short_digest`` is fine (diagnostics itself is documented as a
leaf) -- but note that import is done LOCALLY inside ``render_cli_line``,
not at module level: ``diagnostics.py`` imports ``sandbox_preflight``, and
``sandbox_preflight`` imports THIS module (for ``PullEvent``/
``parse_pull_line``) -- a module-level ``from nethackers.diagnostics import
_short_digest`` here would close that into a real import cycle
(``diagnostics`` -> ``sandbox_preflight`` -> ``pull_events`` ->
``diagnostics``) that breaks depending on which of the three is imported
first. Deferring the import to call time sidesteps it: by the time anything
actually calls ``render_cli_line``, every module along that cycle has
already finished loading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_LAYER_LINE_RE = re.compile(r"^([0-9a-f]{12}): (.*)$")
_DONE_STATUSES = {"Pull complete", "Already exists"}

# docker's per-layer byte progress, as printed on a terminal. Sizes use docker's
# decimal units (go-units HumanSize).
_BYTES_RE = re.compile(
    r"^(Downloading|Extracting)\s+\[[=> ]*\]\s+([\d.]+)\s*([kMGT]?B)/([\d.]+)\s*([kMGT]?B)")
_UNITS = {"B": 1, "kB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
_DOWNLOADED = {"Download complete", "Verifying Checksum", "Pull complete"}


def _bytes(number: str, unit: str) -> int:
    return int(float(number) * _UNITS[unit])


def byte_sums(state: PullParseState) -> tuple[int | None, int | None]:
    """(bytes downloaded, bytes known) over ``state``'s layers; ``(None, None)``
    when no byte line has been seen."""
    if not state.progress:
        return None, None
    return (sum(done for _i, done, _t in state.progress),
            sum(total for _i, _d, total in state.progress))


@dataclass(frozen=True)
class PullEvent:
    """One observable step of a sandbox image's acquisition (``docker
    pull`` or, for the no-layers ``make``-build path, the build itself).

    ``kind``: ``"arena"`` | ``"mutator"`` -- which sandbox this is about.
    ``ref``: the image ref being acquired (``resolve_image``'s output --
      may be a long ``@sha256:...`` digest pin; a consumer that shows this
      to a human should shorten it, e.g. via ``render_cli_line`` below --
      never truncated here, this is data).
    ``phase``: ``"start"`` (about to begin -- emitted once, before anything
      is read), ``"layer"`` (one layer's status changed -- ``docker pull``
      only, never the ``make`` path), ``"done"`` (finished successfully),
      or ``"error"`` (finished, failed).
    ``layers_total`` / ``layers_complete``: running counts of distinct
      layer ids seen / reached a DONE status (``Pull complete`` or
      ``Already exists``) so far. ``None`` when not yet known (``"start"``)
      or not applicable (the ``make``-build path has no layer concept at
      all, so every event on that path carries ``None``/``None``).
    ``detail``: a short, human-oriented scrap of context for this specific
      event (a layer's raw status word, an error's raw text, or ``""`` when
      there's nothing beyond what ``phase`` already says). Not re-parsed by
      anything -- purely for a consumer that wants more than the aggregate
      counts.
    ``bytes_done`` / ``bytes_total``: the byte sums over layers docker has
      printed progress for; ``None`` when there are none.
    """

    kind: str
    ref: str
    phase: str  # "start" | "layer" | "done" | "error"
    layers_total: int | None
    layers_complete: int | None
    detail: str
    bytes_done: int | None = None    # bytes downloaded so far, when docker printed them
    bytes_total: int | None = None   # the layers' sizes seen so far (grows as layers start)


@dataclass(frozen=True)
class PullParseState:
    """Accumulated layer bookkeeping ``parse_pull_line`` folds over one
    pull's worth of stdout lines: every layer id seen at all, and the
    subset that reached a DONE status. Frozen + ``frozenset`` fields so
    there is no in-place ``.add()`` to reach for by accident --
    ``parse_pull_line`` is a pure reducer, and the state passed into it
    must still be valid to reuse or compare after the call, exactly as it
    was."""

    seen: frozenset[str] = field(default_factory=frozenset)
    complete: frozenset[str] = field(default_factory=frozenset)
    # (layer id, bytes downloaded, layer size), sorted by id.
    progress: tuple[tuple[str, int, int], ...] = ()


def parse_pull_line(
    state: PullParseState, line: str, *, kind: str, ref: str,
) -> tuple[PullParseState, PullEvent | None]:
    """Fold one raw ``docker pull`` stdout line into ``state``, returning
    the ``(new_state, event)`` it implies -- ``event`` is ``None`` for a
    line that carries no progress-relevant information (most of the
    header/footer chatter: ``Using default tag: ...``, ``<tag>: Pulling
    from ...``, the ``Digest: sha256:...`` line, the terminal ``Status:
    ...`` line, the final bare ``registry/name:tag`` echo). Pure: ``state``
    itself is never mutated in place -- a fresh instance is always
    returned, and the one passed in remains valid.

    Recognizes docker's per-layer status lines, "<12-hex layer id>: <status>",
    including the byte-progress ones docker prints on a terminal (see the
    module docstring).

    * ``"<12-hex layer id>: <status>"`` -- a per-layer status line. The id
      counts toward ``layers_total`` the first time it's seen, whatever
      the status text says (so an unrecognized future docker status
      string -- or, in practice, ``Waiting``/``Downloading``/``Verifying
      Checksum``/``Extracting`` -- still shows up as "a layer", just not
      yet complete); a status of exactly ``"Pull complete"`` or ``"Already
      exists"`` additionally counts it toward ``layers_complete``. Yields
      ``phase="layer"``.

    Everything else -- including the terminal ``"Status: Downloaded newer
    image for ..."``/``"Status: Image is up to date for ..."`` line -- is
    noise: ``(state, None)``. ``Status:`` used to trigger its own
    ``phase="done"`` event here, but that meant a successful pull
    double-fired ``"done"`` (once from this line, once from
    ``_pull_image``'s own post-loop bracket -- see ``_final_pull_event``).
    Fix round 1 (spec S5.5) makes ``Status:`` noise, same treatment as
    ``Digest:`` right before it, so there is exactly one authoritative
    ``"done"``/``"error"`` per acquisition, always emitted by
    ``sandbox_preflight._pull_image`` (built from this function's own final
    accumulated ``state``) rather than by this per-line parse. A caller
    that wants ``phase="start"``/``"done"``/``"error"`` events gets them
    directly from ``_pull_image``, never from here.
    """
    line = line.rstrip()
    m = _LAYER_LINE_RE.match(line)
    if m is not None:
        layer_id, status = m.group(1), m.group(2)
        seen = state.seen | {layer_id}
        complete = state.complete | {layer_id} if status in _DONE_STATUSES else state.complete
        progress = {i: (done, total) for i, done, total in state.progress}
        size = _BYTES_RE.match(status)
        if size is not None and size.group(1) == "Downloading":
            progress[layer_id] = (_bytes(size.group(2), size.group(3)),
                                  _bytes(size.group(4), size.group(5)))
        elif layer_id in progress and (status in _DOWNLOADED or status.startswith("Extracting")):
            total = progress[layer_id][1]
            progress[layer_id] = (total, total)
        elif status == "Already exists":
            progress.pop(layer_id, None)
        new_state = PullParseState(
            seen=seen, complete=complete,
            progress=tuple(sorted((i, d, t) for i, (d, t) in progress.items())))
        done, known = byte_sums(new_state)
        event = PullEvent(kind=kind, ref=ref, phase="layer", layers_total=len(seen),
                          layers_complete=len(complete), detail=status,
                          bytes_done=done, bytes_total=known)
        return new_state, event
    return state, None


def render_cli_line(event: PullEvent) -> str:
    """A compact, single-line rendering of ``event`` for a plain
    terminal/log consumer -- ``cli.py``'s use of this seam. (The TUI, Task
    5, renders its own richer widget straight from the ``PullEvent``
    stream instead of going through this.)

    Mid-pull (``phase="layer"``), this is just the layer tally:
    ``"pulling arena  7/12 layers"``. On ``"start"``/``"done"``/``"error"``
    -- where the layer counts are either not yet known or no longer the
    interesting number -- the (possibly digest-pinned) ref is shown
    instead, always shortened through ``diagnostics._short_digest`` so a
    64-hex ``@sha256:...`` pin never blows a plain terminal line past one
    line by itself.
    """
    from nethackers.diagnostics import _short_digest  # deferred: see module docstring

    if event.phase == "layer" and event.bytes_done is not None and event.bytes_total:
        return (f"pulling {event.kind}  {event.bytes_done / 1e6:.0f}/"
                f"{event.bytes_total / 1e6:.0f} MB")
    if event.phase == "layer" and event.layers_total is not None:
        return f"pulling {event.kind}  {event.layers_complete}/{event.layers_total} layers"
    short_ref = _short_digest(event.ref)
    if event.phase == "error":
        return f"pull failed: {event.kind}  {short_ref}"
    if event.phase == "done":
        return f"pulled {event.kind}  {short_ref}"
    return f"pulling {event.kind}  {short_ref}"
