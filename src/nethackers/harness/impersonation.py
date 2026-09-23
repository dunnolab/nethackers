"""curl_cffi -- the codex broker's TLS-impersonation dependency -- installed
host-side on demand, never as a packaged dependency.

The codex broker forwards to Cloudflare-fronted ``chatgpt.com``, which 403s a
plain ``httpx`` request: the hop needs a client that impersonates a real
browser's TLS handshake (``curl_cffi``, "chrome"). But ``curl_cffi`` must NOT
land in ``pyproject``/``uv.lock`` -- that lock is an input to the arena/mutator
image build, so adding it would trip the re-pin ceremony and start a new
verified epoch. It also has no place inside the sandbox: the broker runs
host-side, and only the host ever needs it.

So it is installed host-side, on demand, into the very interpreter nethackers
runs in (``sys.executable``): ``nethackers setup`` pre-installs it for codex
users, and the broker self-heals on first use if setup was skipped. Both go
through this module. ``uv`` is preferred because a ``uv tool install``
environment has no ``pip`` of its own, yet ``uv pip install --python <that
interpreter>`` installs into it fine; a plain pip/conda/pipx install has pip,
so that is the fallback.

Leaf module: stdlib only.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# The one package this module installs. chatgpt.com's Cloudflare edge
# fingerprints TLS (JA3), so codex's broker hop impersonates Chrome via this.
IMPERSONATE_DEP = "curl_cffi"

# A generous ceiling: a cold wheel download + install, never an interactive
# wait (the command is non-interactive). Cached, it returns in well under a
# second.
_INSTALL_TIMEOUT_S = 600


def impersonation_available() -> bool:
    """Whether ``curl_cffi`` can be imported in THIS interpreter. Used both to
    decide if setup needs to install it and to short-circuit the broker's
    self-heal."""
    return importlib.util.find_spec(IMPERSONATE_DEP) is not None


# Where uv's own installer puts it. A non-login shell or a service context
# often lacks ``~/.local/bin`` on PATH, yet that same uv created the pip-less
# environment we're extending -- so look here before giving up on uv.
_UV_FALLBACK_PATHS = ("~/.local/bin/uv", "~/.cargo/bin/uv", "/usr/local/bin/uv")


def _resolve_uv(
    which: Callable[[str], str | None], access: Callable[[str, int], bool],
) -> str | None:
    """``uv`` on PATH, else its standard install location. Returns an absolute
    path (so the later exec doesn't depend on PATH), or ``None`` if uv is
    nowhere to be found."""
    found = which("uv")
    if found is not None:
        return found
    for cand in _UV_FALLBACK_PATHS:
        path = os.path.expanduser(cand)
        if access(path, os.X_OK):
            return path
    return None


def install_argv(
    python: str | None = None, *,
    which: Callable[[str], str | None] = shutil.which,
    access: Callable[[str, int], bool] = os.access,
) -> tuple[str, ...]:
    """The command that installs ``curl_cffi`` into ``python``'s environment
    (default: the interpreter nethackers runs in).

    Prefers ``uv``: ``uv pip install --python <interpreter>`` installs into any
    interpreter, INCLUDING a pip-less ``uv tool`` environment (the common way
    nethackers is installed) -- and it is found even off PATH at its standard
    install location (``_resolve_uv``), because a pip-less venv has no other
    way in. Only with no uv anywhere does it fall back to that interpreter's
    own ``pip`` (present for pip/conda/pipx installs)."""
    python = python or sys.executable
    uv = _resolve_uv(which, access)
    if uv is not None:
        return (uv, "pip", "install", "--python", python, IMPERSONATE_DEP)
    return (python, "-m", "pip", "install", IMPERSONATE_DEP)


def ensure_impersonation_dep(
    *,
    python: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> bool:
    """Idempotent: ``True`` if ``curl_cffi`` is importable here, installing it
    first if it is not. ``False`` only when the install itself can't run or
    fails (offline, no ``uv``/``pip``) -- the caller turns that into a
    fail-loud error. Never raises for an install failure."""
    if impersonation_available():
        return True
    argv = install_argv(python, which=which)
    log.warning(
        "codex broker needs %s for TLS impersonation; installing host-side: %s",
        IMPERSONATE_DEP, " ".join(argv),
    )
    try:
        proc = run(list(argv), capture_output=True, text=True, timeout=_INSTALL_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("could not run the %s install (%s)", IMPERSONATE_DEP, exc)
        return False
    if proc.returncode != 0:
        log.warning("%s install exited %s: %s", IMPERSONATE_DEP, proc.returncode,
                    (proc.stderr or "").strip()[-500:])
        return False
    # A fresh install lands in site-packages the running interpreter already has
    # on sys.path; the finders must rescan to see it.
    importlib.invalidate_caches()
    return impersonation_available()


def load_impersonate_session(
    impersonate: str = "chrome", *, ensure: Callable[[], bool] | None = None,
) -> Any:
    """Return a ``curl_cffi`` ``Session`` that impersonates ``impersonate``,
    self-installing ``curl_cffi`` first if it is missing. Raises ``RuntimeError``
    (fail-loud, never a silent fallback that would expose the credential) when
    it can't be made available.

    ``ensure`` defaults to ``ensure_impersonation_dep`` resolved AT CALL TIME
    (not bound into the signature), so a test can ``monkeypatch`` the module
    attribute; pass it explicitly to inject a fake directly."""
    do_ensure = ensure if ensure is not None else ensure_impersonation_dep
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        if not do_ensure():
            raise RuntimeError(
                f"the codex broker forwards to Cloudflare-fronted chatgpt.com, which "
                f"needs TLS impersonation ({IMPERSONATE_DEP}); the automatic host-side "
                f"install failed -- install it yourself with "
                f"`{' '.join(install_argv())}`"
            ) from None
        from curl_cffi import requests as cffi
    return cffi.Session(impersonate=impersonate)
