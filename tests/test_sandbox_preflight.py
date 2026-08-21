# tests/test_sandbox_preflight.py
"""Unit tests for the mandatory-sandbox preflight (harness/sandbox_preflight.py):
the container-runtime check, and the None/message contract ``preflight`` returns
for the CLI and TUI to display. No real docker, network, or login is touched --
``shutil.which`` / the ``run`` callable / ``auth_docker_args`` are injected.
"""
from pathlib import Path

from nethackers.harness import sandbox_preflight as sp
from nethackers.harness.auth_inject import AuthUnavailable


class _Info:
    def __init__(self, returncode):
        self.returncode = returncode


# --- docker_available: PATH lookup + a live `docker info`, not just install --


def test_docker_available_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda name: None)
    assert sp.docker_available() is False


def test_docker_available_false_when_info_returns_nonzero(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda name: "/usr/bin/docker")
    assert sp.docker_available(run=lambda *a, **kw: _Info(1)) is False


def test_docker_available_true_when_info_ok(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda name: "/usr/bin/docker")
    assert sp.docker_available(run=lambda *a, **kw: _Info(0)) is True


def test_docker_available_false_when_info_raises(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda name: "/usr/bin/docker")

    def _raise(*a, **kw):
        raise OSError("daemon gone")
    assert sp.docker_available(run=_raise) is False


# --- preflight: None when good; a styled, hinted message otherwise ---------


def test_preflight_none_when_docker_and_auth_ok(monkeypatch):
    monkeypatch.setattr(sp, "docker_available", lambda **kw: True)
    monkeypatch.setattr(sp, "auth_docker_args", lambda *a, **kw: [])
    assert sp.preflight("codex", system="Linux", home=Path("/h")) is None


def test_preflight_message_when_docker_down(monkeypatch):
    monkeypatch.setattr(sp, "docker_available", lambda **kw: False)
    msg = sp.preflight("codex", system="Linux", home=Path("/h"))
    assert msg is not None
    low = msg.lower()
    assert "sandbox unavailable" in low
    # carries the bring-up hint (colima on mac / docker|podman elsewhere)
    assert "colima" in low or "podman" in low or "docker" in low


def test_preflight_message_when_auth_unavailable(monkeypatch):
    monkeypatch.setattr(sp, "docker_available", lambda **kw: True)

    def _raise(operator, **kw):
        raise AuthUnavailable(operator, "run `codex login` on this host, then retry")
    monkeypatch.setattr(sp, "auth_docker_args", _raise)

    msg = sp.preflight("codex", system="Linux", home=Path("/h"))
    assert msg is not None and "not logged in" in msg.lower()
    assert "codex login" in msg


def test_preflight_passes_operator_to_auth(monkeypatch):
    monkeypatch.setattr(sp, "docker_available", lambda **kw: True)
    seen = {}

    def _capture(operator, **kw):
        seen["operator"] = operator
        return []
    monkeypatch.setattr(sp, "auth_docker_args", _capture)

    sp.preflight("claude", system="Linux", home=Path("/h"))
    assert seen["operator"] == "claude"
