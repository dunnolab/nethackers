# tests/test_sandbox_preflight.py
"""Unit tests for the mandatory-sandbox preflight (harness/sandbox_preflight.py):
the container-runtime check, and the None/message contract ``preflight`` returns
for the CLI and TUI to display. No real docker, network, or login is touched --
``shutil.which`` / the ``run`` callable / ``auth_docker_args`` are injected.
"""
from pathlib import Path
from types import SimpleNamespace

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


# --- preflight no longer gates on the image: it's auto-built on demand -------


def test_preflight_does_not_probe_for_the_image(monkeypatch):
    # The image is auto-provisioned (build_mutator_image), NOT a precondition the
    # user must satisfy -- so preflight (docker + login) never touches it.
    monkeypatch.setattr(sp, "docker_available", lambda **kw: True)
    monkeypatch.setattr(sp, "auth_docker_args", lambda *a, **kw: [])
    called = {"n": 0}
    monkeypatch.setattr(sp, "image_present", lambda *a, **kw: called.update(n=called["n"] + 1))
    assert sp.preflight("codex", system="Linux", home=Path("/h")) is None
    assert called["n"] == 0


def test_image_present_true_on_zero_exit():
    assert sp.image_present("img", run=lambda *a, **k: SimpleNamespace(returncode=0)) is True
    assert sp.image_present("img", run=lambda *a, **k: SimpleNamespace(returncode=1)) is False


# --- auto-build: the first run provisions the image itself (no `make` for users)


class _FakeProc:
    def __init__(self, lines, rc):
        self.stdout = iter(lines)
        self._rc = rc

    def wait(self):
        return self._rc


def test_build_mutator_image_streams_and_succeeds(monkeypatch, tmp_path):
    # a repo root (Dockerfile.mutator + Makefile) is found; `make mutator` runs,
    # its output streams to on_line, rc 0 -> None (success).
    (tmp_path / "Dockerfile.mutator").write_text("x")
    (tmp_path / "Makefile").write_text("x")
    monkeypatch.setattr(sp.Path, "cwd", classmethod(lambda cls: tmp_path))
    seen = {"argv": None, "cwd": None, "lines": []}

    def _popen(argv, **kw):
        seen["argv"], seen["cwd"] = argv, kw.get("cwd")
        return _FakeProc(["step 1/10", "step 2/10"], 0)

    assert sp.build_mutator_image("my/mut:tag", on_line=seen["lines"].append,
                                  popen=_popen) is None
    assert seen["argv"] == ["make", "mutator", "MUTATOR_IMAGE=my/mut:tag"]
    assert seen["cwd"] == str(tmp_path)
    assert seen["lines"] == ["step 1/10", "step 2/10"]


def test_build_mutator_image_reports_build_failure(monkeypatch, tmp_path):
    (tmp_path / "Dockerfile.mutator").write_text("x")
    (tmp_path / "Makefile").write_text("x")
    monkeypatch.setattr(sp.Path, "cwd", classmethod(lambda cls: tmp_path))
    err = sp.build_mutator_image("img", popen=lambda *a, **k: _FakeProc([], 2))
    assert err is not None and "setup failed" in err.lower()


def test_build_mutator_image_errors_outside_the_repo(monkeypatch, tmp_path):
    # no Dockerfile.mutator/Makefile up the tree -> can't build; clear message.
    monkeypatch.setattr(sp.Path, "cwd", classmethod(lambda cls: tmp_path))
    err = sp.build_mutator_image("img", popen=lambda *a, **k: _FakeProc([], 0))
    assert err is not None and "repo" in err.lower()
