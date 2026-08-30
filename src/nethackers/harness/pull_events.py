"""The typed pull-progress event seam (spec S5.5): a small, LEAF vocabulary
for reporting sandbox-image acquisition progress, replacing the untyped
``on_line: Callable[[str], None]`` callback (raw ``docker pull`` stdout
text) that ``sandbox_preflight.py`` used to be the only way to observe a
pull. Both the CLI (``cli.py``) and, later, the TUI's provisioning screen
consume the same ``PullEvent`` stream from the same emitter
(``sandbox_preflight._pull_image``/``ensure_image``) -- one source of truth
for "how far along is this pull", rendered two different ways.

Progress is counted in LAYERS, never bytes. ``docker pull``'s own
non-terminal output aggregates several layers downloading concurrently, each
at its own pace -- turning that into one faithful byte-percent would mean
summing per-layer, in-flight byte counters that docker never reports as a
stable total up front. Layer-count is coarser but exact and monotonic: a
layer is either not yet done or done, counted once each way, with nothing to
drift out of sync. NO image-size field either, for the same reason (size is
deferred by ruling, not merely unimplemented -- do not add one, not even
``Optional``).

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
_STATUS_PREFIX = "Status: "


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
      event (a layer's raw status word, the docker CLI's own terminal
      ``Status: ...`` line, an error's raw text, or ``""`` when there's
      nothing beyond what ``phase`` already says). Not re-parsed by
      anything -- purely for a consumer that wants more than the aggregate
      counts.
    """

    kind: str
    ref: str
    phase: str  # "start" | "layer" | "done" | "error"
    layers_total: int | None
    layers_complete: int | None
    detail: str


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


def parse_pull_line(
    state: PullParseState, line: str, *, kind: str, ref: str,
) -> tuple[PullParseState, PullEvent | None]:
    """Fold one raw ``docker pull`` stdout line into ``state``, returning
    the ``(new_state, event)`` it implies -- ``event`` is ``None`` for a
    line that carries no progress-relevant information (most of the
    header/footer chatter: ``Using default tag: ...``, ``<tag>: Pulling
    from ...``, the ``Digest: sha256:...`` line, the final bare
    ``registry/name:tag`` echo). Pure: ``state`` itself is never mutated in
    place -- a fresh instance is always returned, and the one passed in
    remains valid.

    Recognizes exactly two line shapes -- docker's own stable status
    vocabulary (spec S5.5's ground truth), NOT the in-place byte-progress
    text (``Downloading [==>    ]  3MB/12MB``) that only ever appears when
    docker itself is attached to a real terminal; captured through a pipe
    (always true here -- see ``sandbox_preflight._pull_image``) docker
    prints the plain discrete-line form instead, one line per status
    change, which is exactly what this matches:

    * ``"<12-hex layer id>: <status>"`` -- a per-layer status line. The id
      counts toward ``layers_total`` the first time it's seen, whatever
      the status text says (so an unrecognized future docker status
      string -- or, in practice, ``Waiting``/``Downloading``/``Verifying
      Checksum``/``Extracting`` -- still shows up as "a layer", just not
      yet complete); a status of exactly ``"Pull complete"`` or ``"Already
      exists"`` additionally counts it toward ``layers_complete``. Yields
      ``phase="layer"``.
    * ``"Status: Downloaded newer image for ..."`` / ``"Status: Image is
      up to date for ..."`` -- the one line docker always prints exactly
      once, right at the end of a successful pull (the ``Digest:`` line
      just before it is deliberately treated as noise, not a second
      trigger -- ``Status:`` is the one line guaranteed to appear exactly
      once). Yields ``phase="done"`` with the running counts as they
      stand (by now usually ``layers_complete == layers_total``, though a
      docker daemon using the containerd snapshotter has been observed to
      report far fewer per-layer transitions than the classic engine --
      see ``tests/test_pull_events.py`` -- so don't assume equality).

    A caller that wants ``phase="start"``/``"error"`` events gets them
    directly from ``sandbox_preflight._pull_image`` (before/after this
    per-line loop), never from here.
    """
    line = line.rstrip()
    m = _LAYER_LINE_RE.match(line)
    if m is not None:
        layer_id, status = m.group(1), m.group(2)
        seen = state.seen | {layer_id}
        complete = state.complete | {layer_id} if status in _DONE_STATUSES else state.complete
        new_state = PullParseState(seen=seen, complete=complete)
        event = PullEvent(kind=kind, ref=ref, phase="layer", layers_total=len(seen),
                          layers_complete=len(complete), detail=status)
        return new_state, event
    if line.startswith(_STATUS_PREFIX):
        total = len(state.seen) if state.seen else None
        complete_n = len(state.complete) if state.seen else None
        event = PullEvent(kind=kind, ref=ref, phase="done", layers_total=total,
                          layers_complete=complete_n, detail=line)
        return state, event
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

    if event.phase == "layer" and event.layers_total is not None:
        return f"pulling {event.kind}  {event.layers_complete}/{event.layers_total} layers"
    short_ref = _short_digest(event.ref)
    if event.phase == "error":
        return f"pull failed: {event.kind}  {short_ref}"
    if event.phase == "done":
        return f"pulled {event.kind}  {short_ref}"
    return f"pulling {event.kind}  {short_ref}"
