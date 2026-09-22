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
Evolve adds a third, after both images are acquired:
``sandbox_platform_mismatch`` refuses an arena and mutator built for different
platforms, because the coding agent scores its own candidates inside the
mutator.

``ensure_image`` makes a ``resolve_image``-produced ref actually present: a
GHCR digest pin is always ``docker pull``ed -- that now includes the arena
even inside a repo checkout (spec 2026-09-14 D6) -- and only a non-digest tag
reachable from a checkout (the mutator's content-fingerprint tag) is built via
``make`` -- never the reverse (INV11: a digest ref can't be `-t`-tagged by a
build, so it is only ever pulled).

Kept a leaf module (stdlib + containers + auth_inject + pull_events + setup.host
+ ptyrun only, all themselves leaves too) so both ``cli`` and the Textual form
can import it without a cycle.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path

from rich.markup import escape

from nethackers import _image_pins, image_inputs, ptyrun
from nethackers.containers import container_runtime
from nethackers.harness.auth_inject import AuthUnavailable, auth_docker_args
from nethackers.harness.pull_events import PullEvent, PullParseState, byte_sums, parse_pull_line
from nethackers.setup.host import setup_supported


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


def image_platform(image: str, *, runtime: str = "docker", run=subprocess.run) -> str | None:
    """The ``os/arch`` (e.g. ``linux/amd64``) of a LOCAL image, from ``<runtime>
    image inspect``; ``None`` when it can't be read (absent, runtime down, or a
    record missing either field)."""
    try:
        result = run([runtime, "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}",
                      image], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    os_name, _, arch = value.partition("/")
    return value if os_name and arch else None


def _repo_root() -> Path | None:
    """The nethackers repo checkout (the dir holding ``Dockerfile.mutator`` +
    ``Makefile``), searched from CWD upward -- the build context the sandbox
    image is built from. ``None`` when nethackers isn't being run from its repo
    (then the image can't be auto-built and must be pulled/built out of band)."""
    for base in (Path.cwd(), *Path.cwd().parents):
        if (base / "Dockerfile.mutator").is_file() and (base / "Makefile").is_file():
            return base
    return None


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
    (spec §5.1; for the mutator, spec 2026-09-15 §5.5; for the arena, spec
    2026-09-14 D6): an explicit value (flag / env -- anything that made the
    layered Stage field non-None) wins verbatim; otherwise the ARENA always
    resolves to its pinned GHCR digest, checkout or not, because that pin is
    what declares the reference architecture. The mutator resolves to the image
    matching the checkout's own files: the pinned digest when they are the
    pinned build's inputs, else the fingerprint tag
    ``nethackers/mutator:h-<64 hex>``; outside a checkout, its pinned digest.
    NO side effects -- it only reads files, never builds or pulls (safe in
    EvolveParams default factories). ``repo_root`` injectable for tests."""
    if explicit is not None:
        return explicit
    root = repo_root()
    if root is None:
        return _PIN[kind]
    if kind == "mutator":
        return _checkout_mutator_ref(root)
    # The arena pin names ONE platform's bytes and is what declares the
    # reference architecture (spec 2026-09-14 D2/D6). A locally built tag is
    # unclassified by construction and the hub refuses evidence from it, so a
    # repo checkout must NOT silently substitute one. Reach a local build
    # deliberately: --image, or NETHACKERS_ARENA_IMAGE.
    return _PIN[kind]


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


def mutator_platform_args(image: str) -> list[str]:
    """``--platform`` for any ``<runtime> run`` of the mutator ``image`` (a run's
    operator container, discovery's probes), mirroring the arena's
    (eval/runner.py). The pin and a checkout's fingerprint tag are built for the
    reference platform, so there the flag silences the platform-mismatch
    warning Docker would otherwise print as the first line of every iteration's
    output, and keeps those runs from depending on how an image store resolves
    an unflagged run. Any other ref gets nothing: it may be a local build for
    another platform, which ``docker run --platform`` refuses to run at all.
    ``sandbox_platform_mismatch`` is what stops such a ref from being used for
    evolve."""
    if image == _image_pins.MUTATOR_IMAGE or is_local_mutator_fingerprint(image):
        return ["--platform", image_inputs.REFERENCE_PLATFORM]
    return []


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
    # --platform: the arena's platform, never the host's. A host-native build on
    # Apple Silicon is arm64 NetHack, which plays a different game per seed.
    return _run_build(
        [runtime, "build", "--platform", image_inputs.REFERENCE_PLATFORM,
         "-f", "Dockerfile.mutator",
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


def manifest_layers(ref: str, *, runtime: str, run=subprocess.run) -> dict[str, int] | None:
    """Layer digest -> download size for ``ref`` as the registry lists it (the
    linux/amd64 entry of a multi-arch index), from ``<runtime> manifest
    inspect -v``. ``None`` when it can't be read: offline, or a runtime (e.g.
    Podman) that prints another shape."""
    try:
        result = run([runtime, "manifest", "inspect", "-v", ref], capture_output=True,
                     text=True, timeout=MANIFEST_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        doc = json.loads(result.stdout)
    except ValueError:
        return None
    entries = doc if isinstance(doc, list) else [doc]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        platform_ = (entry.get("Descriptor") or {}).get("platform") or {}
        if len(entries) > 1 and (platform_.get("os"), platform_.get("architecture")) != (
                "linux", "amd64"):
            continue
        manifest = entry.get("SchemaV2Manifest") or entry.get("OCIManifest") or {}
        layers = manifest.get("layers")
        if isinstance(layers, list):
            try:
                return {layer["digest"]: int(layer["size"]) for layer in layers}
            except (KeyError, TypeError, ValueError):
                return None
    return None


def download_size(pull: Sequence[str], present: Sequence[str], *, runtime: str,
                  run=subprocess.run) -> int | None:
    """Bytes a pull of the ``pull`` refs will download: their layers counted
    once, minus layers the ``present`` images already have. ``None`` if any
    ``pull`` manifest can't be read."""
    wanted: dict[str, int] = {}
    for ref in pull:
        layers = manifest_layers(ref, runtime=runtime, run=run)
        if layers is None:
            return None
        wanted.update(layers)
    for ref in present:
        for digest in manifest_layers(ref, runtime=runtime, run=run) or {}:
            wanted.pop(digest, None)
    return sum(wanted.values())


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
    done, known = byte_sums(state)
    return PullEvent(kind=kind, ref=ref, phase=phase, layers_total=total,
                     layers_complete=complete, detail=detail,
                     bytes_done=done, bytes_total=known)


# A byte update at most this often: docker redraws every layer many times a
# second, and neither the CLI bar nor the TUI needs more.
_BYTE_EVENT_INTERVAL = 0.1
_REAL_POPEN = subprocess.Popen


def _pty_lines(proc, master: int):
    """Every line a pty child prints, until it exits."""
    for batch in ptyrun.read_lines(master, done=lambda: proc.poll() is not None):
        if batch:
            yield from batch


def _pull_image(ref: str, kind: str, *, runtime: str = "docker", on_line=None,
                on_event: Callable[[PullEvent], None] | None = None,
                popen=subprocess.Popen, tty: bool | None = None,
                spawn=ptyrun.spawn, clock=time.monotonic) -> str | None:
    """``docker pull ref``, streaming each output line to ``on_line`` (raw
    text, back-compat) and folding it through ``parse_pull_line`` into typed
    ``PullEvent``s for ``on_event`` (spec S5.5): a ``phase="start"`` event
    first, exactly one final ``"done"``/``"error"`` event last (via
    ``_final_pull_event``), and ``"layer"`` events between -- byte-only
    updates at most every ``_BYTE_EVENT_INTERVAL`` seconds.

    The real pull runs in a pseudo-terminal (``tty``; ``None`` means "yes for
    the real ``subprocess.Popen``, no for an injected fake"), so docker prints
    its byte progress; the pipe path stays for injected ``popen`` fakes and
    for platforms without a pty. Ctrl-C stops the pull before propagating.

    ``None`` on success, else one of the styled, one-runnable-command messages
    (spec S5.5 / INV9) mapped from the registry's failure shape."""
    if on_event is not None:
        on_event(PullEvent(kind=kind, ref=ref, phase="start",
                           layers_total=None, layers_complete=None, detail=""))
    state = PullParseState()
    use_tty = (popen is _REAL_POPEN) if tty is None else tty
    argv = [runtime, "pull", ref]
    last_byte_event = float("-inf")
    proc = None
    try:
        if use_tty and ptyrun.available():
            proc, master = spawn(argv)
            lines = _pty_lines(proc, master)
        else:
            proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         bufsize=1)
            lines = (ln.rstrip() for ln in proc.stdout)
        collected: list[str] = []
        for stripped in lines:
            collected.append(stripped + "\n")
            if on_line is not None:
                on_line(stripped)
            if on_event is not None:
                state, event = parse_pull_line(state, stripped, kind=kind, ref=ref)
                if event is None:
                    continue
                if event.detail.startswith(("Downloading", "Extracting")):
                    now = clock()
                    if now - last_byte_event < _BYTE_EVENT_INTERVAL:
                        continue
                    last_byte_event = now
                on_event(event)
        rc = proc.wait()
    except KeyboardInterrupt:
        if proc is not None:
            ptyrun.stop(proc)
        raise
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
# arena ref and a mutator ref are never textually identical -- `_PIN` and the
# mutator's fingerprint tag always bake the kind into the name -- so `ref`
# alone is already a
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


def preflight_runtime(*, run=subprocess.run, scope: str | None = None) -> str | None:
    """``None`` if a working container runtime is available; else the styled
    "no container runtime" message, ending in the one command that brings one
    up (``nethackers setup``, narrowed with ``--for <scope>`` when the caller
    knows what it needs). The half of the old combined ``preflight`` every
    sandboxed command needs -- ``eval``/``submit``/``evolve`` all score or
    mutate inside a container (spec S5.5: arena has no operator)."""
    if docker_available(run=run):
        return None
    if setup_supported(platform.system()):
        todo = f"run `nethackers setup{f' --for {scope}' if scope else ''}` to set one up"
    else:
        todo = "start Docker or Podman"
    return f"[red]sandbox unavailable[/]: no working container runtime found — {todo}, then retry"


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


def sandbox_platform_mismatch(arena_image: str, mutator_image: str, *,
                              runtime: str = "docker", run=subprocess.run) -> str | None:
    """``None`` unless evolve's two sandboxes are LOCAL images for different
    platforms; else the styled message saying which one to rebuild.

    The coding agent scores its candidates with the arena kit inside the
    mutator, and NetHack generates a different dungeon from the same seed on
    another architecture. A mismatched pair therefore has the agent optimizing
    games the arena never plays: every change it measures as a win is judged
    on other games. Only a definite mismatch refuses -- a platform that can't
    be read is not evidence of one. Both images must already be present
    (``ensure_image``), so call this after acquisition."""
    arena = image_platform(arena_image, runtime=runtime, run=run)
    mutator = image_platform(mutator_image, runtime=runtime, run=run)
    if arena is None or mutator is None or arena == mutator:
        return None
    kind = "mutator" if mutator != image_inputs.REFERENCE_PLATFORM else "arena"
    ref = mutator_image if kind == "mutator" else arena_image
    if kind == "mutator" and mutator_platform_args(ref):
        # The pin or our fingerprint tag: acquisition fetches or builds it for
        # the reference platform again once the local copy is gone.
        fix = (f"Remove the {kind} image so the next run fetches it again: "
               f"`{runtime} image rm {escape(ref)}`")
    elif "@" in ref:
        fix = (f"The {kind} image is pinned by digest, so it can't be rebuilt: "
               f"drop its image override")
    else:
        fix = (f"Rebuild the {kind} image for {image_inputs.REFERENCE_PLATFORM} with "
               f"`make {kind} {_MAKE_VAR[kind]}={escape(ref)}`, or drop its image override")
    return (f"[red]sandbox platform mismatch[/] — the mutator image runs {mutator} but "
            f"the arena image runs {arena}, so the coding agent would test its changes "
            f"on different NetHack games than the ones they are scored on. {fix}")
