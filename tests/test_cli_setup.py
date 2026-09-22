"""`nethackers setup` wiring: flags reach the flow, and the hub login it runs
is the same device flow `nethackers login` uses. The flow itself is faked."""
from __future__ import annotations

import nethackers.cli as cli


def test_flags_reach_the_flow(monkeypatch):
    seen = {}

    def fake_run_setup(opts, deps):
        seen["opts"] = opts
        return 0

    monkeypatch.setattr(cli.setup_flow, "run_setup", fake_run_setup)
    assert cli.main(["setup", "--for", "eval", "--operator", "codex", "--yes"]) == 0
    opts = seen["opts"]
    assert (opts.scope, opts.operator, opts.yes) == ("eval", "codex", True)
    assert opts.interactive is False          # pytest's stdin is not a terminal


def test_setup_exit_code_is_the_flows(monkeypatch):
    monkeypatch.setattr(cli.setup_flow, "run_setup", lambda opts, deps: 1)
    assert cli.main(["setup"]) == 1


def test_the_hub_login_is_the_same_device_flow_as_nethackers_login(monkeypatch):
    got = {}

    def fake_run_setup(opts, deps):
        got["login"] = deps.hub_login()
        return 0

    saved = []
    monkeypatch.setattr(cli.setup_flow, "run_setup", fake_run_setup)
    monkeypatch.setattr(cli, "device_login",
                        lambda **kw: {"access_token": "t", "refresh_token": "r", "expires_in": 0})
    monkeypatch.setattr(cli, "whoami_from_token", lambda tok, **_k: "castiel")
    monkeypatch.setattr(cli._cred, "save", saved.append)       # never the real credentials file
    assert cli.main(["setup"]) == 0
    assert got["login"] == "castiel" and saved[0].login == "castiel"


def test_setup_is_listed_in_help():
    assert "setup" in cli._build_parser(cli.load_stage()).format_help()


# --- end to end in a real terminal (the only test that reaches the real
# isatty gate, Confirm.ask and the stdin/stderr wiring) -----------------------

_DRIVER = '''
import sys
from types import SimpleNamespace

from nethackers import cli
from nethackers.containers import RuntimeCandidate, RuntimeReport
from nethackers.diagnostics import CHECK_SPECS, CheckResult
from nethackers.setup.host import HostFacts


def checks(**status):
    return [CheckResult(id=c, status=status.get(c, "ok"), severity=s, detail="d",
                        fix=None, capabilities=caps) for c, (s, caps) in CHECK_SPECS.items()]


results = iter([checks(mutator_image="warn"), checks()])
cli.run_checks = lambda **kw: next(results)
cli.detect_host = lambda: HostFacts("Darwin", "arm64", brew=True, host_rosetta=True)
cli.probe_container_runtime = lambda: RuntimeReport(
    "docker", (RuntimeCandidate("docker", "usable", ""),))
cli.gh_state = lambda: ("you", "authed")
cli._load_creds = lambda: SimpleNamespace(login="you")
cli.preflight_operator = lambda op: None
cli._setup_pull = lambda kinds, total: print("PULLED", ",".join(kinds)) or None
cli._setup_pull_size = lambda kinds: 432_000_000
# Pretend every tool is installed, and make sure nothing real can ever run.
cli.setup_flow.resolve_exe = lambda name, **kw: f"/usr/local/bin/{name}"


def _never(*args, **kwargs):
    raise AssertionError("the end-to-end test must not run real commands")


cli.setup_runner.run_captured = _never
cli.setup_runner.run_terminal = _never
sys.exit(cli.main(["setup"]))
'''


def _drive(script, answers, timeout=60.0) -> str:
    """Run ``script`` with a pseudo-terminal as its whole terminal; whenever the
    output contains the next ``(prompt, reply)`` prompt, type the reply."""
    import os
    import pty
    import select
    import sys
    import time

    pid, fd = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, str(script)])
    out, pending, deadline = b"", list(answers), time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if pending and pending[0][0] in out:
            os.write(fd, pending.pop(0)[1])
    os.waitpid(pid, 0)
    return out.decode("utf-8", "replace")


def test_setup_end_to_end_in_a_real_terminal(tmp_path):
    import os

    import pytest
    if not hasattr(os, "fork"):
        pytest.skip("needs pty.fork")
    script = tmp_path / "drive.py"
    script.write_text(_DRIVER)
    out = _drive(script, [(b"Continue?", b"y\r")])
    assert "Checking this machine" in out
    assert "pull the mutator image" in out and "432 MB, first time only" in out
    assert "PULLED mutator" in out
    assert "ready to eval" in out and "Next: nethackers evolve" in out
