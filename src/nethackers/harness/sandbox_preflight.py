"""Preflight + acquisition for the mandatory arena/mutator sandboxes, shared by
the CLI and the TUI.

Everything here runs in a container -- there is no host-execution path -- so
before a run starts we verify a working container runtime, acquire the image,
and (mutator only) a resolvable host login for the selected harness, failing
fast with one clear, styled message instead of a mid-run crash. Every
``str | None``-returning function here follows the same contract: ``None``
means "good to go", else a Rich-markup string to display (CLI stderr / TUI
``#f_err`` line).

Two separate gates (spec S5.5) -- do NOT fuse them back together:
``preflight_runtime`` (a working container runtime) is needed by every
sandboxed command, including a plain ``eval``/``submit``; ``preflight_operator``
(a resolvable ``codex``/``claude`` host login) is needed ONLY by ``evolve`` --
the arena has no operator, so eval/submit must never call it. ``preflight``
stays as a thin backward-compatible wrapper over both, for evolve's use.

``ensure_image`` makes a ``resolve_image``-produced ref actually present:
builds the arena's local ``:dev`` tag via ``make``, and a checkout's mutator by
content fingerprint, or ``docker pull``s a GHCR digest pin otherwise -- never
the reverse (INV11: a digest ref can't be `-t`-tagged by a build, so it is
only ever pulled).

Kept a leaf module (stdlib + containers + auth_inject + pull_events only, all
themselves leaves too) so both ``cli`` and the Textual form can import it
without a cycle.
"""
from __future__ import annotations

import platform
import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path

from rich.markup import escape

from nethackers import _image_pins, image_inputs
from nethackers.containers import container_runtime
from nethackers.harness.auth_inject import AuthUnavailable, auth_docker_args
from nethackers.harness.pull_events import PullEvent, PullParseState, parse_pull_line


def sandbox_hint() -> str:
    """How to bring a container runtime up, per-OS. macOS has no native Docker
    daemon (Docker Desktop is out per the mutator-sandbox spec) -- its fix is a
    VM, not just "start Docker"."""
    if platform.system() == "Darwin":
        return (
            "start one, e.g. `colima start --cpu 6 --memory 12 --vm-type vz "
            "--mount-type virtiofs` (or Podman)"
        )
    return "start Docker or Podman"


def docker_available(*, run=subprocess.run) -> bool:
    """True iff a usable container runtime (``docker`` OR ``podman``) is present
    -- a thin bool over ``containers.container_runtime`` for callers that only
    need yes/no (``preflight``). The richer per-CLI diagnosis ``doctor`` renders
    is ``containers.probe_container_runtime``. Kept named ``docker_available``
    for its existing callers, but "docker" here means "a docker-compatible
    runtime", podman included (issue #50): a binary on PATH whose ``info`` still
    fails (a stopped Colima VM, a socket permission denial) is not usable."""
    return container_runtime(run=run) is not None


def image_present(image: str, *, runtime: str = "docker", run=subprocess.run) -> bool:
    """Is the image available locally? Cheap ``<runtime> image inspect`` (no
    pull). Used to decide whether to acquire it (``ensure_image``) and
    by discovery, so an unbuilt image degrades quietly instead of silently
    reporting the host CLI's models. ``runtime`` is the resolved container CLI
    (``container_runtime()``); callers thread it through rather than re-probing,
    and it defaults to ``"docker"`` for back-compat."""
    try:
        return run([runtime, "image", "inspect", image],
                   capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _repo_root() -> Path | None:
    """The nethackers repo checkout (the dir holding ``Dockerfile.mutator`` +
    ``Makefile``), searched from CWD upward -- the build context the sandbox
    image is built from. ``None`` when nethackers isn't being run from its repo
    (then the image can't be auto-built and must be pulled/built out of band)."""
    for base in (Path.cwd(), *Path.cwd().parents):
        if (base / "Dockerfile.mutator").is_file() and (base / "Makefile").is_file():
            return base
    return None


_LOCAL_DEV_REF = {"arena": "nethackers/arena:dev"}
_PIN = {"arena": _image_pins.ARENA_IMAGE, "mutator": _image_pins.MUTATOR_IMAGE}

# A checkout's own mutator builds (spec 2026-09-15 §5.5): content-addressed tags
# in this local repository, never pushed; CI publishes the same tags under the
# pinned image's GHCR repository.
LOCAL_MUTATOR_REPO = "nethackers/mutator"
_GHCR_MUTATOR_REPO = _image_pins.MUTATOR_IMAGE.partition("@")[0]
_MUTATOR_BUILD_LABEL = "org.dunnolab.nethackers.image=mutator"
_FINGERPRINT_REF_RE = re.compile(rf"{re.escape(LOCAL_MUTATOR_REPO)}:h-[0-9a-f]{{64}}")


def resolve_image(explicit: str | None, kind: str, *, repo_root=_repo_root) -> str:
    """The image ref to use for ``kind`` (``"arena"``/``"mutator"``). Ladder
    (spec §5.1; for the mutator, spec 2026-09-15 §5.5): an explicit value (flag /
    env -- anything that made the layered Stage field non-None) wins verbatim;
    outside a repo checkout, the pinned GHCR digest. Inside a checkout the arena
    uses its locally-built dev tag, and the mutator the image matching the
    checkout's own files: the pinned digest when they are the pinned build's
    inputs, else the fingerprint tag ``nethackers/mutator:h-<64 hex>``.
    NO side effects -- it only reads files, never builds or pulls (safe in
    EvolveParams default factories). ``repo_root`` injectable for tests."""
    if explicit is not None:
        return explicit
    root = repo_root()
    if root is None:
        return _PIN[kind]
    if kind == "mutator":
        return _checkout_mutator_ref(root)
    return _LOCAL_DEV_REF[kind]


def _checkout_mutator_ref(root: Path) -> str:
    try:
        inputs = image_inputs.mutator_inputs_hash(root, _image_pins.NLE_BASE_IMAGE)
    except OSError:
        return _PIN["mutator"]    # an incomplete checkout: the released image still works
    if inputs == _image_pins.MUTATOR_INPUTS:
        return _image_pins.MUTATOR_IMAGE
    return f"{LOCAL_MUTATOR_REPO}:{image_inputs.image_tag(inputs)}"


def is_local_mutator_fingerprint(ref: str) -> bool:
    """``nethackers/mutator:h-<64 hex>``: a checkout's content-addressed mutator."""
    return _FINGERPRINT_REF_RE.fullmatch(ref) is not None


def ghcr_mutator_ref(local_ref: str) -> str:
    """The name CI publishes a local fingerprint ref under."""
    return f"{_GHCR_MUTATOR_REPO}:{local_ref.partition(':')[2]}"


_MAKE_VAR = {"arena": "ARENA_IMAGE", "mutator": "MUTATOR_IMAGE"}
_DOCKERFILE = {"arena": "arena/Dockerfile", "mutator": "Dockerfile.mutator"}
_ENV_VAR = {"arena": "NETHACKERS_ARENA_IMAGE", "mutator": "NETHACKERS_MUTATOR_IMAGE"}


def _build_image(image: str, kind: str, *, on_line=None,
                 on_event: Callable[[PullEvent], None] | None = None,
                 popen=subprocess.Popen, repo_root=_repo_root) -> str | None:
    """``make {kind} {KIND}_IMAGE={image}`` from the repo root, streaming each
    build line to ``on_line`` (see ``_run_build``). ``None`` on success, else a
    styled error. Never called on a digest ref (INV11) -- ``ensure_image``
    guards that. ``repo_root`` injectable so a caller that already decided
    "we're in a repo" builds from the exact root it checked."""
    root = repo_root()
    if root is None:
        return (f"[red]can't set up the sandbox[/]: run nethackers from its repo "
                f"(the {kind} image builds from {_DOCKERFILE[kind]} there)")
    return _run_build(["make", kind, f"{_MAKE_VAR[kind]}={image}"], cwd=root, image=image,
                      kind=kind, on_line=on_line, on_event=on_event, popen=popen)


_BUILD_TAIL_LINES = 15


def _run_build(argv: list[str], *, cwd: Path, image: str, kind: str, on_line=None,
               on_event: Callable[[PullEvent], None] | None = None,
               popen=subprocess.Popen) -> str | None:
    """Run a build command in ``cwd``, streaming each line to ``on_line``.
    ``on_event``, when given, gets exactly two ``PullEvent``s bracketing the
    build -- ``phase="start"`` before, ``phase="done"``/``phase="error"`` after.
    ``layers_total``/``layers_complete`` are always ``None``: a build has no
    layer concept (that vocabulary is ``docker pull``-only).

    A failed build's error event (raw) and message (escaped for Rich markup)
    carry its last non-empty output lines: callers that pass only ``on_event``
    show no build output, so this is the only place the cause can surface."""
    if on_event is not None:
        on_event(PullEvent(kind=kind, ref=image, phase="start",
                           layers_total=None, layers_complete=None, detail=""))
    tail: deque[str] = deque(maxlen=_BUILD_TAIL_LINES)
    try:
        proc = popen(argv, cwd=str(cwd),
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            stripped = line.rstrip()
            if stripped:
                tail.append(stripped)
            if on_line is not None:
                on_line(stripped)
        rc = proc.wait()
    except (OSError, subprocess.SubprocessError) as exc:
        if on_event is not None:
            on_event(PullEvent(kind=kind, ref=image, phase="error",
                               layers_total=None, layers_complete=None, detail=str(exc)))
        return f"[red]sandbox setup failed[/]: {exc}"
    if rc != 0:
        last_lines = "\n".join(tail)
        if on_event is not None:
            on_event(PullEvent(kind=kind, ref=image, phase="error",
                               layers_total=None, layers_complete=None, detail=last_lines))
        if not last_lines:
            return (f"[red]sandbox setup failed[/] — the {kind} image build did not complete "
                    f"(exit code {rc})")
        return (f"[red]sandbox setup failed[/] — the {kind} image build did not complete. "
                f"Its last lines:\n{escape(last_lines)}")
    if on_event is not None:
        on_event(PullEvent(kind=kind, ref=image, phase="done",
                           layers_total=None, layers_complete=None, detail=""))
    return None


def _acquire_mutator_fingerprint(ref: str, *, runtime: str, on_line, on_event, popen, run,
                                 repo_root) -> str | None:
    """Fill a checkout's fingerprint ref: pull CI's image for the same files and
    tag it locally when GHCR has it. Otherwise -- or if that pull or tag fails --
    build it locally on the pinned base instead. The pull's ``error`` event never
    reaches ``on_event``: a build follows, and its outcome is the one to report.
    No cleanup of older fingerprint images (spec 2026-09-15 D9): a run starts a
    fresh container every iteration, so nothing holds an image between
    iterations, and removing "old" fingerprints from one worktree could break an
    evolve running in another."""
    remote = ghcr_mutator_ref(ref)
    if _remote_image_exists(remote, runtime=runtime, run=run):
        err = _pull_image(remote, "mutator", runtime=runtime, on_line=on_line,
                          on_event=_without_errors(on_event), popen=popen)
        if err is None:
            err = _tag_image(remote, ref, runtime=runtime, run=run)
        if err is None:
            return None
    root = repo_root()
    if root is None:
        return ("[red]can't set up the sandbox[/]: run nethackers from its repo "
                "(the mutator image builds from Dockerfile.mutator there)")
    return _run_build(
        [runtime, "build", "-f", "Dockerfile.mutator",
         "--build-arg", f"NLE_BASE={_image_pins.NLE_BASE_IMAGE}",
         "--label", _MUTATOR_BUILD_LABEL, "-t", ref, "."],
        cwd=root, image=ref, kind="mutator", on_line=on_line, on_event=on_event,
        popen=popen)


def _without_errors(on_event: Callable[[PullEvent], None] | None
                    ) -> Callable[[PullEvent], None] | None:
    """``on_event`` with every ``phase="error"`` event dropped."""
    if on_event is None:
        return None

    def forward(event: PullEvent) -> None:
        if event.phase != "error":
            on_event(event)
    return forward


# How long a registry reachability probe may take. `manifest inspect` walks every
# sub-manifest of a multi-arch index, which measured 7-16s against GHCR on a laptop
# (and longer for an index with many platforms), so a tighter budget makes a
# reachable image look gone. `diagnostics._manifest_reachable` shares this number:
# doctor must never call an image unreachable that acquisition would pull happily.
MANIFEST_PROBE_TIMEOUT = 30


def _remote_image_exists(ref: str, *, runtime: str, run) -> bool:
    try:
        result = run([runtime, "manifest", "inspect", ref], capture_output=True,
                     timeout=MANIFEST_PROBE_TIMEOUT)
        return bool(result.returncode == 0)
    except (OSError, subprocess.SubprocessError):
        return False


def _tag_image(source: str, target: str, *, runtime: str, run) -> str | None:
    try:
        result = run([runtime, "tag", source, target], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"[red]sandbox setup failed[/]: {exc}"
    if result.returncode != 0:
        return f"[red]sandbox setup failed[/] — couldn't tag {source} as {target}"
    return None


def _final_pull_event(kind: str, ref: str, state: PullParseState, phase: str,
                      *, detail: str = "") -> PullEvent:
    """The one ``PullEvent`` ``_pull_image`` emits itself once its ``Popen``
    loop is over (success or failure) -- as opposed to the ``"layer"``
    events ``parse_pull_line`` produces mid-loop. Carries the FINAL tally
    from ``state`` (``None``/``None`` only if no layer line was ever seen at
    all, e.g. a fully-cached pull that goes straight to ``Status: Image is
    up to date`` with no per-layer lines in between) so a caller that only
    watches for ``"done"``/``"error"`` still gets a real number, not just a
    bare marker."""
    total = len(state.seen) if state.seen else None
    complete = len(state.complete) if state.seen else None
    return PullEvent(kind=kind, ref=ref, phase=phase, layers_total=total,
                     layers_complete=complete, detail=detail)


def _pull_image(ref: str, kind: str, *, runtime: str = "docker", on_line=None,
                on_event: Callable[[PullEvent], None] | None = None,
                popen=subprocess.Popen) -> str | None:
    """``docker pull ref``, streaming combined stdout/stderr to ``on_line``
    (raw text, kept for back-compat -- no existing caller is forced to
    migrate) AND, when given, folding each line through ``parse_pull_line``
    into typed ``PullEvent``s for ``on_event`` (spec S5.5) -- the two
    callbacks are independent and both fire off the same read loop.
    ``on_event`` additionally gets a ``phase="start"`` event before anything
    is read, and exactly one final ``phase="done"``/``phase="error"`` event
    once the process exits (via ``_final_pull_event``, built from the
    loop's final accumulated ``state``) -- ``parse_pull_line`` itself never
    produces a ``"done"``/``"error"`` event (its terminal ``Status: ...``
    line is deliberately treated as noise, same as ``Digest:``; see its own
    docstring), so this one post-loop event is the SOLE done/error signal
    on every path, pull success, pull failure, or a ``popen``/read
    exception.

    ``None`` on success, else one of the styled, one-runnable-command
    messages (spec S5.5 / INV9) mapped from the registry's failure shape."""
    if on_event is not None:
        on_event(PullEvent(kind=kind, ref=ref, phase="start",
                           layers_total=None, layers_complete=None, detail=""))
    state = PullParseState()
    try:
        proc = popen([runtime, "pull", ref], stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, bufsize=1)
        collected: list[str] = []
        for line in proc.stdout:
            collected.append(line)
            stripped = line.rstrip()
            if on_line is not None:
                on_line(stripped)
            if on_event is not None:
                state, event = parse_pull_line(state, stripped, kind=kind, ref=ref)
                if event is not None:
                    on_event(event)
        rc = proc.wait()
    except (OSError, subprocess.SubprocessError) as exc:
        if on_event is not None:
            on_event(_final_pull_event(kind, ref, state, "error", detail=str(exc)))
        return _pull_error_message(kind, str(exc))
    if rc != 0:
        if on_event is not None:
            on_event(_final_pull_event(kind, ref, state, "error",
                                       detail="".join(collected).strip()))
        return _pull_error_message(kind, "".join(collected))
    if on_event is not None:
        on_event(_final_pull_event(kind, ref, state, "done"))
    return None


def _pull_error_message(kind: str, text: str) -> str:
    """Map a failed ``docker pull``'s output (or the exception raised trying
    to run it) to exactly one thing to do next (INV9) -- never a raw registry
    error or traceback. Checked most-specific-first: a missing manifest (never
    published, or a typo'd ref) before a bare "not found", since GHCR's auth-
    or-missing wording overlaps."""
    low = text.lower()
    if "manifest unknown" in low or "not found" in low or "404" in low:
        return (f"[red]no published sandbox for this build[/] — clone the repo, "
                f"or set `{_ENV_VAR[kind]}`")
    if "denied" in low or "unauthorized" in low or "403" in low:
        return "[red]stale ghcr login[/] — run `docker logout ghcr.io`, then retry"
    return ("[red]couldn't reach the registry[/] — only the first run needs the "
           "network; a pulled image keeps working, so retry once you're online")


# In-process leader/follower dedup for `ensure_image` (spec S5.5): a second
# concurrent `ensure_image(ref)` call for the SAME ref must not start a second
# `docker pull`/`make` build -- it waits for the in-flight one, then re-checks
# rather than assuming success. Keyed on the resolved `ref` string alone (an
# arena ref and a mutator ref are never textually identical -- `_LOCAL_DEV_REF`
# / `_PIN` always bake the kind into the name -- so `ref` alone is already a
# unique key, no need for a compound `(kind, ref)` one). Process-local only:
# a SECOND `nethackers` process racing this one is not covered (cross-process
# locking, e.g. a lockfile, is explicitly out of scope for this seam) -- only
# concurrent callers *within* one process (the TUI's own async workers, or a
# test) are deduped.
_inflight_lock = threading.Lock()
_inflight_pulls: dict[str, threading.Event] = {}


def ensure_image(ref: str, kind: str, *, runtime: str = "docker", on_line=None,
                 on_event: Callable[[PullEvent], None] | None = None,
                 popen=subprocess.Popen, run=subprocess.run,
                 repo_root=_repo_root) -> str | None:
    """Make the already-resolved ``ref`` (``resolve_image``'s output -- INV2,
    never a raw config field) available locally, acquiring it if not. ``kind``
    is ``"arena"``/``"mutator"``. ``None`` on success, else a styled error
    ending in exactly one runnable command (INV9).

    Branches, checked in this order:
      1. already present locally (cheap ``image_present``) -> nothing to do.
      2. a GHCR digest pin (``@sha256:`` in ``ref``) -> ``docker pull`` it,
         mapping the registry's failure shapes to one-command messages. This
         check comes BEFORE the repo-checkout check -- INV11 ("never build a
         digest ref") is a hard safety rule, so it wins even in the (today
         unreachable via ``resolve_image``'s own ladder, but not impossible if
         someone hand-sets an env override) case of a repo checkout with an
         explicit pinned ref.
      3. a checkout's mutator fingerprint (``nethackers/mutator:h-<hash>``) ->
         pull CI's image for the same files and tag it locally; if GHCR has no
         such image, or that pull or tag fails, build it on the pinned base
         instead.
      4. else, inside a nethackers checkout (``_repo_root``) -> ``make {kind}``
         builds it -- the local dev tag, or any other non-digest tag asked for.
      5. else (a custom non-digest ref with no repo to build it from) ->
         best-effort ``docker pull`` -- it's the caller's own registry ref.

    Concurrent calls for the SAME ``ref`` (within this process) are deduped:
    exactly one of them actually pulls/builds (the "leader") while the rest
    ("followers") block on a ``threading.Event`` instead of each starting
    their own redundant ``docker pull``. A follower that wakes up re-checks
    ``image_present`` rather than trusting the leader succeeded -- if the
    leader failed, the image still isn't there, and the (former) follower
    loops back around and becomes the new leader itself, retrying the
    acquisition exactly as if it had been the only caller all along.
    """
    while True:
        if image_present(ref, runtime=runtime, run=run):
            return None

        with _inflight_lock:
            event = _inflight_pulls.get(ref)
            if event is None:
                event = threading.Event()
                _inflight_pulls[ref] = event
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            event.wait()
            continue  # leader's done (or failed) -- loop re-checks image_present

        try:
            if "@sha256:" in ref:
                return _pull_image(ref, kind, runtime=runtime, on_line=on_line,
                                   on_event=on_event, popen=popen)
            if is_local_mutator_fingerprint(ref):
                return _acquire_mutator_fingerprint(ref, runtime=runtime, on_line=on_line,
                                                    on_event=on_event, popen=popen, run=run,
                                                    repo_root=repo_root)
            if repo_root() is not None:
                return _build_image(ref, kind, on_line=on_line, on_event=on_event, popen=popen,
                                    repo_root=repo_root)
            return _pull_image(ref, kind, runtime=runtime, on_line=on_line,
                               on_event=on_event, popen=popen)
        finally:
            with _inflight_lock:
                _inflight_pulls.pop(ref, None)
            event.set()


def preflight_runtime(*, run=subprocess.run) -> str | None:
    """``None`` if a working container runtime is available; else the styled
    "no container runtime" message. The half of the old combined ``preflight``
    every sandboxed command needs -- ``eval``/``submit``/``evolve`` all score
    or mutate inside a container -- so this alone is the correct (and only)
    preflight for a plain ``eval``/``submit`` (spec S5.5: arena has no
    operator)."""
    if not docker_available(run=run):
        return (
            f"[red]sandbox unavailable[/]: no working container runtime found "
            f"— {sandbox_hint()}, then retry"
        )
    return None


def preflight_operator(operator: str, *, system: str | None = None,
                       home: Path | None = None) -> str | None:
    """``None`` if a resolvable host login exists for ``operator`` (the
    token/creds the mutator container reuses); else the styled "not logged
    in" message. ``evolve``-only: the arena has no operator, so a plain
    ``eval``/``submit`` must NEVER call this (spec S5.5's "two separate
    gates" -- fusing them back together is exactly the bug this split
    fixes).

    OpenCode 2 is always usable: without a provider key it runs OpenCode's
    free models. Returning early also keeps this check from writing the
    sandbox's provider-config copy (``auth_inject``), since doctor calls it."""
    if operator == "opencode2":
        return None
    try:
        auth_docker_args(operator, system=system or platform.system(),
                         home=home or Path.home(), _require_exists=True)
    except AuthUnavailable as exc:
        return f"[red]not logged in[/]: {exc.hint}"
    return None


def preflight(operator: str, *, system: str | None = None,
              home: Path | None = None, run=subprocess.run) -> str | None:
    """``None`` if the mutator sandbox can run; else a styled, human-facing
    error. Backward-compatible combined gate -- ``preflight_runtime`` then
    ``preflight_operator`` -- kept for evolve's call sites (which need both).
    New code that only needs the runtime half (``eval``/``submit``) should
    call ``preflight_runtime`` directly rather than this. The image itself is
    auto-provisioned (``ensure_image``), not a precondition checked here."""
    msg = preflight_runtime(run=run)
    if msg is not None:
        return msg
    return preflight_operator(operator, system=system, home=home)
