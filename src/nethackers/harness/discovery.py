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
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from nethackers.containers import container_name, label_args
from nethackers.harness.auth_inject import (
    AuthUnavailable,
    auth_docker_args,
    opencode2_global_providers,
    opencode2_has_provider_key,
)
from nethackers.harness.sandbox_preflight import image_present, mutator_platform_args

_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=100"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"   # required on the Bearer (OAuth) tiers only


def _image_which(name: str) -> str:
    """A ``which`` that always resolves: the codex/claude binaries are baked
    into the mutator image, so container-mode discovery never gates on a host
    PATH lookup (a failed ``docker run`` is what surfaces a truly-missing one)."""
    return name


def _container_run(image: str, *, docker: str = "docker", home: Path | None = None,
                   run: Callable = subprocess.run) -> Callable:
    """A ``run`` adapter that executes the coding-agent CLIs INSIDE the mutator
    image -- so the version + (version-filtered) catalog reflect exactly what a
    run actually uses, not the host's possibly-different CLI. Host-only helpers
    (the macOS keychain probe) still run on the host. Auth is the same
    mount/token a real run gets; if the login can't be resolved the probe simply
    runs unauthenticated and returns fewer/no models -- warn, never block."""
    def _run(argv, **kw):
        binary = argv[0] if argv else ""
        if binary in ("codex", "claude", "opencode2"):
            try:
                auth = auth_docker_args(
                    binary, system=platform.system(), home=home or Path.home(),
                )
            except AuthUnavailable:
                auth = []
            return run([docker, "run", *mutator_platform_args(image), "--rm",
                        "--name", container_name("probe"),
                        *label_args(), *auth, image, *argv], **kw)
        return run(argv, **kw)   # host-side (e.g. the `security` keychain read)
    return _run


@dataclass(frozen=True)
class ModelInfo:
    id: str                       # slug / model id passed to --model / -m
    label: str                    # display name for the picker
    reasoning: tuple[str, ...] = ()   # supported effort levels (may be empty)
    deprecated: bool = False
    # Empty ``reasoning`` has two meanings: older catalogs did not report the
    # metadata, while OpenCode's config can positively say a model has no
    # variants.  The picker must distinguish those cases or it offers generic
    # efforts that OpenCode rejects with ``Variant unavailable``.
    reasoning_known: bool = False


_PROBE_SEP = "@@nh-probe@@"


def _run_image_script(image: str, binary: str, script: str, *,
                      docker: str = "docker", run: Callable,
                      home: Path | None = None) -> str | None:
    """ONE ``<docker> run`` of ``bash -lc <script>`` in the mutator image, with
    the same auth a real run gets. Returns stdout (whatever was captured, even
    on a non-zero last command -- earlier echoes still printed), or ``None`` if
    the run couldn't start. Amortizes the runtime's ~1s startup across every
    probe. ``docker`` is the resolved container CLI (docker/podman -- issue
    #50), threaded from the caller; defaults to ``"docker"``."""
    try:
        auth = auth_docker_args(binary, system=platform.system(), home=home or Path.home())
    except AuthUnavailable:
        auth = []
    try:
        proc = run([docker, "run", *mutator_platform_args(image), "--rm",
                    "--name", container_name("probe"),
                    *label_args(), *auth, image, "bash", "-lc", script],
                   capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout


def probe_operator(
    backend: str,
    *,
    image: str,
    docker: str = "docker",
    run: Callable = subprocess.run,
    http: Callable = httpx.get,
    home: Path | None = None,
) -> tuple[CliInfo, list[ModelInfo] | None]:
    """Detect the container CLI + its catalog in ONE ``docker run`` (vs the ~1s
    each of separate ``detect_cli``/``list_models`` probes) -- for the TUI model
    picker, which refreshes on every operator switch. Codex gets version + login
    + catalog from a single combined command; claude's catalog stays the HTTP
    ``/v1/models`` (account-gated, version-independent). OpenCode 2's "logged
    in" means a global provider has a key, read on the host: the container
    never sees OpenCode's own login store. Never raises; an unbuilt image /
    unparseable output degrades to (not-installed / no-version, None)."""
    binary = {"codex": "codex", "claude": "claude", "opencode2": "opencode2"}[backend]
    if not image_present(image, runtime=docker, run=run):
        return CliInfo(backend, False, None, None), None
    if backend == "codex":
        script = (f"codex --version; echo {_PROBE_SEP}; "
                  f"(codex login status >/dev/null 2>&1 && echo OK || echo NO); "
                  f"echo {_PROBE_SEP}; codex debug models")
    elif backend == "claude":
        script = f"claude --version; echo {_PROBE_SEP}; claude auth status --json 2>/dev/null"
    else:
        script = f"opencode2 --version; echo {_PROBE_SEP}; {_OPENCODE2_SETTLED_MODELS}"
    parts = (_run_image_script(
        image, binary, script, docker=docker, run=run, home=home,
    ) or "").split(_PROBE_SEP)
    version = parts[0].strip() or None if parts else None
    if backend == "codex":
        logged_in = ("OK" in parts[1]) if len(parts) > 1 else None
        models: list[ModelInfo] | None = None
        if len(parts) > 2:
            try:
                models = _codex_parse(json.loads(parts[2]))
            except Exception:
                models = None
        return CliInfo("codex", True, version, logged_in), models
    if backend == "opencode2":
        host_home = home or Path.home()
        variants = _opencode2_config_variants(home=host_home)
        models = _opencode2_parse(parts[1], variants=variants) if len(parts) > 1 else None
        return (CliInfo("opencode2", True, version, opencode2_has_provider_key(home=host_home)),
                models)
    logged_in = None
    if len(parts) > 1:
        try:
            logged_in = bool(json.loads(parts[1]).get("loggedIn"))
        except Exception:
            logged_in = None
    # claude's catalog is account-gated (version-independent) -> host HTTP, not
    # another container round-trip.
    claude_models = _claude_models(run=run, http=http, home=home)
    return CliInfo("claude", True, version, logged_in), claude_models


def list_models(
    backend: str,
    *,
    image: str | None = None,
    docker: str = "docker",
    run: Callable = subprocess.run,
    http: Callable = httpx.get,
    home: Path | None = None,
) -> list[ModelInfo] | None:
    if image is not None:
        if not image_present(image, runtime=docker, run=run):
            return None   # image not built -> unknown; never the host CLI's cache
        if backend == "opencode2":
            # The settling loop has to run inside ONE container: a fresh
            # container per `opencode2 models` call never gets past the empty
            # snapshot.
            output = _run_image_script(image, "opencode2", _OPENCODE2_SETTLED_MODELS,
                                       docker=docker, run=run, home=home)
            return _opencode2_parse(
                output or "", variants=_opencode2_config_variants(home=home or Path.home()),
            )
        # probe the mutator container (via the resolved runtime), not the host
        run = _container_run(image, docker=docker, home=home, run=run)
    if backend == "codex":
        return _codex_models(run=run, home=home, allow_cache=image is None)
    if backend == "claude":
        return _claude_models(run=run, http=http, home=home)
    if backend == "opencode2":
        return _opencode2_models(
            run=run, variants=_opencode2_config_variants(home=home or Path.home()),
        )
    return None


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _opencode2_parse(
    output: str, *, variants: dict[str, tuple[str, ...]] | None = None,
) -> list[ModelInfo] | None:
    """Parse ``opencode2 models`` and attach config-declared variants.

    OpenCode's models command deliberately prints only ``provider/model`` IDs;
    variants live in the resolved config.  When ``variants`` is supplied we
    know that absence means "this model has no named variant", rather than
    "discovery did not provide metadata".
    """
    models: list[ModelInfo] = []
    for raw in output.splitlines():
        model_id = _ANSI.sub("", raw).strip()
        if not model_id or "/" not in model_id or model_id.startswith("Error:"):
            continue
        models.append(ModelInfo(
            id=model_id,
            label=model_id,
            reasoning=variants.get(model_id, ()) if variants is not None else (),
            reasoning_known=variants is not None,
        ))
    return models or None


# `opencode2 models` prints the background service's current model snapshot,
# which is empty until the service has loaded its catalog -- always the case on
# the first call in a fresh container, where it settled in ~3s when measured.
# Poll until two non-empty readings agree (max ~15s), then print the last one.
_OPENCODE2_SETTLED_MODELS = (
    "prev=; i=0; while [ $i -lt 15 ]; do out=$(opencode2 models 2>/dev/null); "
    'if [ -n "$out" ] && [ "$out" = "$prev" ]; then break; fi; '
    "prev=$out; i=$((i + 1)); sleep 1; done; "
    "printf '%s\\n' \"$out\""
)


def _opencode2_models(
    *, run: Callable, variants: dict[str, tuple[str, ...]] | None = None,
) -> list[ModelInfo] | None:
    try:
        proc = run(["sh", "-c", _OPENCODE2_SETTLED_MODELS],
                   capture_output=True, text=True, timeout=40)
        if proc.returncode == 0:
            return _opencode2_parse(proc.stdout or "", variants=variants)
    except Exception:
        pass
    return None


def _opencode2_config_variants(*, home: Path) -> dict[str, tuple[str, ...]]:
    """Named OpenCode variants per ``provider/model``, from the global config
    only -- the same providers a run gets (``auth_inject``).

    Both OpenCode 2's list form (``[{"id": "high"}]``) and the mapping form
    (``{"high": {...}}``) are accepted. Like OpenCode, later files add to a
    model's variants instead of replacing them, so a file that only renames a
    model keeps the variants declared elsewhere.
    """
    found: dict[str, tuple[str, ...]] = {}
    for _name, providers in opencode2_global_providers(home=home):
        for provider_id, provider in providers.items():
            if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
                continue
            for model_id, model in provider["models"].items():
                raw = model.get("variants") if isinstance(model, dict) else None
                names: list[str] = []
                if isinstance(raw, dict):
                    names = [str(name) for name in raw]
                elif isinstance(raw, list):
                    names = [str(item["id"]) for item in raw
                             if isinstance(item, dict) and item.get("id")]
                key = f"{provider_id}/{model_id}"
                found[key] = tuple(dict.fromkeys([*found.get(key, ()), *names]))
    return found


def _codex_reasoning(levels: object) -> tuple[str, ...]:
    # `supported_reasoning_levels` is a list of {"effort", "description"} dicts in
    # the real `codex debug models` output (older builds may emit bare strings).
    # Keep just the effort NAME as a string -- storing the dicts crashes the
    # `nethackers models` table renderer (``", ".join`` over non-strings).
    if not isinstance(levels, list):
        return ()
    out: list[str] = []
    for lv in levels:
        if isinstance(lv, dict) and isinstance(lv.get("effort"), str):
            out.append(lv["effort"])
        elif isinstance(lv, str):
            out.append(lv)
    return tuple(out)


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
        out.append(ModelInfo(
            id=str(slug),
            label=str(m.get("display_name") or slug),
            reasoning=_codex_reasoning(m.get("supported_reasoning_levels")),
            deprecated=m.get("upgrade") is not None,
        ))
    return out


def _codex_models(*, run: Callable, home: Path | None,
                  allow_cache: bool = True) -> list[ModelInfo] | None:
    try:
        proc = run(["codex", "debug", "models"], capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and (proc.stdout or "").strip():
            parsed = _codex_parse(json.loads(proc.stdout))
            if parsed is not None:
                return parsed
    except Exception:
        pass
    if not allow_cache:
        return None   # container probe: the HOST's ~/.codex cache is the wrong version
    cache = (home or Path.home()) / ".codex" / "models_cache.json"
    try:
        return _codex_parse(json.loads(cache.read_text()))
    except Exception:
        return None


def _keychain_token(*, run: Callable) -> str | None:
    try:
        proc = run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                   capture_output=True, text=True, timeout=5)
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)["claudeAiOauth"]["accessToken"] or None
    except Exception:
        return None


def _claude_auth_headers(*, run: Callable, home: Path | None) -> dict[str, str] | None:
    if platform.system() == "Darwin":
        tok = _keychain_token(run=run)
        if tok:
            return {"Authorization": f"Bearer {tok}", "anthropic-beta": _ANTHROPIC_OAUTH_BETA}
    creds = (home or Path.home()) / ".claude" / ".credentials.json"
    try:
        tok = json.loads(creds.read_text())["claudeAiOauth"]["accessToken"]
        if tok:
            return {"Authorization": f"Bearer {tok}", "anthropic-beta": _ANTHROPIC_OAUTH_BETA}
    except Exception:
        pass
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return {"x-api-key": key}
    return None


_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max", "ultra")


def _claude_reasoning(capabilities: object) -> tuple[str, ...]:
    # /v1/models exposes capabilities.effort = {"supported": bool,
    # "<level>": {"supported": bool}, ...}. Collect the supported effort level
    # names in canonical low..max order (skipping the top-level "supported" flag).
    if not isinstance(capabilities, dict):
        return ()
    effort = capabilities.get("effort")
    if not isinstance(effort, dict) or not effort.get("supported"):
        return ()
    return tuple(
        level for level in _EFFORT_ORDER
        if isinstance(effort.get(level), dict) and effort[level].get("supported")
    )


def _claude_models(*, run: Callable, http: Callable, home: Path | None) -> list[ModelInfo] | None:
    auth = _claude_auth_headers(run=run, home=home)
    if auth is None:
        return None
    headers = {**auth, "anthropic-version": _ANTHROPIC_VERSION}
    try:
        resp = http(_ANTHROPIC_MODELS_URL, headers=headers, timeout=10)
    except Exception:
        return None
    if getattr(resp, "status_code", None) != 200:
        return None      # 401 stale-token / any non-200 -> unknown, never []
    try:
        data = resp.json().get("data")
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out: list[ModelInfo] = []
    for d in data:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        out.append(ModelInfo(id=str(d["id"]), label=str(d.get("display_name") or d["id"]),
                             reasoning=_claude_reasoning(d.get("capabilities"))))
    return out


_CLAUDE_ALIASES = frozenset({"default", "sonnet", "opus", "haiku", "fable"})


def is_model_available(
    backend: str,
    model: str,
    *,
    models: list[ModelInfo] | None = None,
    image: str | None = None,
    run: Callable = subprocess.run,
    http: Callable = httpx.get,
    home: Path | None = None,
) -> bool | None:
    if backend == "claude" and model in _CLAUDE_ALIASES:
        return True
    if models is None:
        models = list_models(backend, image=image, run=run, http=http, home=home)
    if models is None:
        return None
    check = model
    if backend == "claude" and check.endswith("[1m]"):
        check = check[:-4]
    if backend == "opencode2" and "#" in check:
        # OpenCode 2 selects a variant as `provider/model#variant`.
        check, variant = check.split("#", 1)
        served = next((m for m in models if m.id == check), None)
        if served is None:
            return False
        return (variant in served.reasoning) if served.reasoning_known else True
    return check in {m.id for m in models}


@dataclass(frozen=True)
class CliInfo:
    backend: str
    installed: bool
    version: str | None
    logged_in: bool | None


def _logged_in(backend: str, *, run: Callable) -> bool | None:
    try:
        if backend == "codex":
            return run(["codex", "login", "status"], capture_output=True,
                       text=True, timeout=10).returncode == 0
        if backend == "opencode2":
            # A global provider key, read on the host: the sandbox never sees
            # OpenCode's own login store, so `auth list` would mislead.
            return opencode2_has_provider_key(home=Path.home())
        proc = run(["claude", "auth", "status", "--json"], capture_output=True,
                   text=True, timeout=10)
        if proc.returncode != 0:
            return None
        return bool(json.loads(proc.stdout).get("loggedIn"))
    except Exception:
        return None


def detect_cli(backend: str, *, image: str | None = None, docker: str = "docker",
               run: Callable = subprocess.run, which: Callable = shutil.which) -> CliInfo:
    if image is not None:
        if not image_present(image, runtime=docker, run=run):
            return CliInfo(backend, False, None, None)   # image not built
        run, which = _container_run(image, docker=docker, run=run), _image_which
    binary = {"codex": "codex", "claude": "claude", "opencode2": "opencode2"}[backend]
    if which(binary) is None:
        return CliInfo(backend, False, None, None)
    version: str | None = None
    try:
        proc = run([binary, "--version"], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            version = (proc.stdout or "").strip() or None
    except Exception:
        pass
    return CliInfo(backend, True, version, _logged_in(backend, run=run))


@dataclass(frozen=True)
class Preflight:
    action: str                     # "proceed" | "refuse" | "warn"
    message: str
    cli: CliInfo
    models: list[ModelInfo] | None


def preflight_model(
    backend: str,
    model: str | None,
    *,
    image: str | None = None,
    docker: str = "docker",
    run: Callable = subprocess.run,
    http: Callable = httpx.get,
    home: Path | None = None,
    which: Callable = shutil.which,
) -> Preflight:
    cli = detect_cli(backend, image=image, docker=docker, run=run, which=which)
    if not cli.installed:
        return Preflight("refuse", f"{backend} is not installed / not on PATH.", cli, None)
    # OpenCode 2 without a provider key still serves OpenCode's free models,
    # so only a pinned model it can't serve (checked below) refuses.
    if cli.logged_in is False and backend != "opencode2":
        login_cmd = {"codex": "codex login", "claude": "claude auth login"}[backend]
        return Preflight("refuse", f"{backend} is not logged in — run `{login_cmd}`.", cli, None)
    if not model:
        return Preflight("proceed", "", cli, None)   # harness default: nothing pinned to check
    if backend == "claude" and model in _CLAUDE_ALIASES:
        return Preflight("proceed", "", cli, None)   # aliases are always valid -- skip the probe
    models = list_models(backend, image=image, docker=docker, run=run, http=http, home=home)
    ver = f" {cli.version}" if cli.version else ""
    if models is None:
        return Preflight(
            "warn",
            f"Couldn't verify '{model}' on {backend}{ver} — proceeding; "
            "the run will stop fast if the model is rejected.",
            cli, None)
    avail = is_model_available(backend, model, models=models)
    if avail is True:
        return Preflight("proceed", "", cli, models)
    served = ", ".join(m.id for m in models) or "(none)"
    hint = {
        "codex": "run `codex update`",
        "claude": "check your account access",
        "opencode2": "check the providers in your global opencode.json",
    }[backend]
    return Preflight(
        "refuse",
        f"{backend}{ver} can't serve '{model}'. Available: {served}. {hint} or pick one of those.",
        cli, models)
