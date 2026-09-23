"""curl_cffi installed host-side on demand: which command, and the self-heal."""
from __future__ import annotations

import subprocess
import sys

import pytest

from nethackers.harness import impersonation as imp


def _done(argv, code=0):
    return subprocess.CompletedProcess(argv, code, "", "")


def test_install_argv_prefers_uv_and_targets_the_given_interpreter():
    argv = imp.install_argv("/opt/py", which=lambda n: "/usr/bin/uv" if n == "uv" else None)
    assert argv == ("uv", "pip", "install", "--python", "/opt/py", "curl_cffi")


def test_install_argv_defaults_to_this_interpreter():
    assert imp.install_argv(which=lambda n: "/usr/bin/uv")[4] == sys.executable


def test_install_argv_falls_back_to_that_interpreters_pip_without_uv():
    # A pip-based install has pip; without uv this is how we reach it.
    assert imp.install_argv("/opt/py", which=lambda n: None) == (
        "/opt/py", "-m", "pip", "install", "curl_cffi")


def test_ensure_short_circuits_when_already_importable(monkeypatch):
    monkeypatch.setattr(imp, "impersonation_available", lambda: True)
    ran: list = []
    ok = imp.ensure_impersonation_dep(run=lambda *a, **k: ran.append(a) or _done(a),
                                      which=lambda n: "/uv")
    assert ok is True
    assert ran == []  # nothing installed: it was already there


def test_ensure_installs_then_confirms_available(monkeypatch):
    seen = iter([False, True])  # missing, then present after the install
    monkeypatch.setattr(imp, "impersonation_available", lambda: next(seen))
    calls: list = []
    monkeypatch.setattr(imp.importlib, "invalidate_caches", lambda: None)
    ok = imp.ensure_impersonation_dep(
        run=lambda argv, **k: calls.append(argv) or _done(argv), which=lambda n: "/uv")
    assert ok is True
    assert calls and calls[0][0] == "uv"  # it actually shelled out to install


def test_ensure_false_when_the_install_exits_nonzero(monkeypatch):
    monkeypatch.setattr(imp, "impersonation_available", lambda: False)
    ok = imp.ensure_impersonation_dep(
        run=lambda argv, **k: _done(argv, code=1), which=lambda n: "/uv")
    assert ok is False


def test_ensure_false_when_the_install_cannot_even_run(monkeypatch):
    monkeypatch.setattr(imp, "impersonation_available", lambda: False)

    def boom(argv, **k):
        raise FileNotFoundError("no uv, no pip")

    assert imp.ensure_impersonation_dep(run=boom, which=lambda n: None) is False


def test_load_session_fails_loud_when_missing_and_install_fails(monkeypatch):
    # Force the import to fail regardless of whether curl_cffi is installed here.
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    with pytest.raises(RuntimeError, match="TLS impersonation"):
        imp.load_impersonate_session("chrome", ensure=lambda: False)


def test_load_session_returns_a_session_when_curl_cffi_is_present():
    pytest.importorskip("curl_cffi")
    assert imp.load_impersonate_session("chrome") is not None
