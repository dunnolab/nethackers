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
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

_ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models?limit=100"
_ANTHROPIC_VERSION = "2023-06-01"
_ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"   # required on the Bearer (OAuth) tiers only


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
    if backend == "claude":
        return _claude_models(run=run, http=http, home=home)
    return None


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
    run: Callable = subprocess.run,
    http: Callable = httpx.get,
    home: Path | None = None,
) -> bool | None:
    if backend == "claude" and model in _CLAUDE_ALIASES:
        return True
    if models is None:
        models = list_models(backend, run=run, http=http, home=home)
    if models is None:
        return None
    check = model
    if backend == "claude" and check.endswith("[1m]"):
        check = check[:-4]
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
        proc = run(["claude", "auth", "status", "--json"], capture_output=True,
                   text=True, timeout=10)
        if proc.returncode != 0:
            return None
        return bool(json.loads(proc.stdout).get("loggedIn"))
    except Exception:
        return None


def detect_cli(backend: str, *, run: Callable = subprocess.run,
               which: Callable = shutil.which) -> CliInfo:
    binary = {"codex": "codex", "claude": "claude"}[backend]
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
    run: Callable = subprocess.run,
    http: Callable = httpx.get,
    home: Path | None = None,
    which: Callable = shutil.which,
) -> Preflight:
    cli = detect_cli(backend, run=run, which=which)
    if not cli.installed:
        return Preflight("refuse", f"{backend} is not installed / not on PATH.", cli, None)
    if cli.logged_in is False:
        login_cmd = "codex login" if backend == "codex" else "claude auth"
        return Preflight("refuse", f"{backend} is not logged in — run `{login_cmd}`.", cli, None)
    if not model:
        return Preflight("proceed", "", cli, None)   # harness default: nothing pinned to check
    if backend == "claude" and model in _CLAUDE_ALIASES:
        return Preflight("proceed", "", cli, None)   # aliases are always valid -- skip the probe
    models = list_models(backend, run=run, http=http, home=home)
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
    hint = "run `codex update`" if backend == "codex" else "check your account access"
    return Preflight(
        "refuse",
        f"{backend}{ver} can't serve '{model}'. Available: {served}. {hint} or pick one of those.",
        cli, models)
