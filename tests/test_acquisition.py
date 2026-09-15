# tests/test_acquisition.py
"""Acquisition (spec S5.5): making a resolved image ref (``resolve_image``,
Task 1) actually available -- ``ensure_image`` builds a repo-checkout's local
``:dev``/``:latest`` tag via ``make``, or ``docker pull``s a GHCR digest pin,
mapping the pull's failure shapes to one-runnable-command messages (INV9).
Never builds a digest ref (INV11).

Also covers the ``preflight`` split: ``preflight_runtime`` (container runtime
only) vs ``preflight_operator`` (host login only) -- ``arena`` has no
operator, so a plain ``eval``/``submit`` must never demand one (spec S5.5's
"two separate gates"). No real docker/network/login is touched anywhere here
-- ``run``/``popen``/``_repo_root`` are injected fakes.
"""
from __future__ import annotations

from pathlib import Path

from nethackers.harness import sandbox_preflight as sp


class _Info:
    def __init__(self, returncode):
        self.returncode = returncode


class _FakeProc:
    def __init__(self, lines, rc):
        self.stdout = iter(lines)
        self._rc = rc

    def wait(self):
        return self._rc


def _present(ok: bool):
    """A fake ``run`` for ``image_present``: True -> rc 0 (present), False -> rc 1."""
    return lambda *a, **kw: _Info(0 if ok else 1)


# --- ensure_image: already present -> a no-op, nothing built or pulled -----


def test_ensure_image_noop_when_already_present():
    calls = {"build": 0, "pull": 0}

    def _popen(argv, **kw):
        calls["pull" if argv[:2] == ["docker", "pull"] else "build"] += 1
        return _FakeProc([], 0)

    err = sp.ensure_image("nethackers/arena:dev", "arena", run=_present(True),
                          popen=_popen, repo_root=lambda: None)
    assert err is None
    assert calls == {"build": 0, "pull": 0}


# --- a pin (`@sha256:`) ref -> always `docker pull`, even inside a repo ----
# (INV11: never build a digest ref -- the pin check must win over repo_root).


def test_pin_ref_pulls_even_inside_a_repo(tmp_path):
    seen = {}

    def _popen(argv, **kw):
        seen["argv"] = argv
        return _FakeProc(["Pulling fs layer", "Pull complete"], 0)

    ref = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "a" * 64
    err = sp.ensure_image(ref, "arena", run=_present(False), popen=_popen,
                          repo_root=lambda: tmp_path)  # inside a repo -- must NOT build

    assert err is None
    assert seen["argv"][:2] == ["docker", "pull"]
    assert seen["argv"][2] == ref
    assert seen["argv"][0] != "make"


def test_pin_ref_streams_pull_output_to_on_line():
    lines: list[str] = []
    ref = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "b" * 64

    def popen(argv, **kw):
        return _FakeProc(["layer 1/3", "layer 2/3", "layer 3/3"], 0)

    err = sp.ensure_image(ref, "mutator", run=_present(False), popen=popen,
                          on_line=lines.append, repo_root=lambda: None)

    assert err is None
    assert lines == ["layer 1/3", "layer 2/3", "layer 3/3"]


# --- a local dev tag inside a repo checkout -> `make <kind> <KIND>_IMAGE=ref`


def test_dev_tag_in_repo_builds_via_make(tmp_path):
    seen = {}

    def _popen(argv, **kw):
        seen["argv"], seen["cwd"] = argv, kw.get("cwd")
        return _FakeProc(["Step 1/5"], 0)

    err = sp.ensure_image("nethackers/arena:dev", "arena", run=_present(False),
                          popen=_popen, repo_root=lambda: tmp_path)

    assert err is None
    assert seen["argv"] == ["make", "arena", "ARENA_IMAGE=nethackers/arena:dev"]
    assert seen["cwd"] == str(tmp_path)


def test_build_failure_is_styled_without_a_traceback(tmp_path):
    err = sp.ensure_image("nethackers/arena:dev", "arena", run=_present(False),
                          popen=lambda *a, **k: _FakeProc([], 1), repo_root=lambda: tmp_path)
    assert err is not None
    assert "Traceback" not in err
    assert "setup failed" in err.lower()


# --- no repo, not a pin (a custom tag set outside a checkout) -> best-effort pull


def test_custom_non_pin_ref_outside_a_repo_falls_back_to_pull():
    seen = {}

    def _popen(argv, **kw):
        seen["argv"] = argv
        return _FakeProc([], 0)

    err = sp.ensure_image("my/arena:custom", "arena", run=_present(False),
                          popen=_popen, repo_root=lambda: None)

    assert err is None
    assert seen["argv"] == ["docker", "pull", "my/arena:custom"]


# --- pull failures map to ONE runnable command each (INV9), never a raw
# docker/network error -------------------------------------------------


def test_pull_404_gives_one_backticked_command(tmp_path):
    def popen(argv, **kw):
        return _FakeProc(["Error response from daemon: manifest unknown"], 1)
    ref = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "c" * 64

    err = sp.ensure_image(ref, "arena", run=_present(False), popen=popen,
                          repo_root=lambda: None)

    assert err is not None
    assert "Traceback" not in err
    assert err.count("`") == 2                       # exactly one backticked token
    assert "NETHACKERS_ARENA_IMAGE" in err
    assert "clone the repo" in err.lower()


def test_pull_404_names_the_mutator_env_var_for_mutator_kind():
    def popen(argv, **kw):
        return _FakeProc(["404 Not Found"], 1)
    ref = "ghcr.io/dunnolab/nethackers-mutator@sha256:" + "d" * 64

    err = sp.ensure_image(ref, "mutator", run=_present(False), popen=popen,
                          repo_root=lambda: None)

    assert err is not None and err.count("`") == 2
    assert "NETHACKERS_MUTATOR_IMAGE" in err


def test_pull_denied_says_docker_logout(tmp_path):
    def popen(argv, **kw):
        return _FakeProc(
            ["Error response from daemon: pull access denied, repository does not "
             "exist or may require 'docker login': denied"], 1)
    ref = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "e" * 64

    err = sp.ensure_image(ref, "arena", run=_present(False), popen=popen,
                          repo_root=lambda: None)

    assert err is not None
    assert "Traceback" not in err
    assert err.count("`") == 2
    assert "docker logout ghcr.io" in err


def test_pull_offline_reports_one_time_network_need():
    def _raise(*a, **kw):
        raise OSError("dial tcp: lookup ghcr.io: no such host")
    ref = "ghcr.io/dunnolab/nethackers-arena@sha256:" + "f" * 64

    err = sp.ensure_image(ref, "arena", run=_present(False), popen=_raise,
                          repo_root=lambda: None)

    assert err is not None
    assert "Traceback" not in err
    assert "first run" in err.lower() and "network" in err.lower()


# --- preflight split: runtime-only vs operator-only -------------------------


def test_preflight_runtime_none_when_docker_ok(monkeypatch):
    monkeypatch.setattr(sp, "docker_available", lambda **kw: True)
    assert sp.preflight_runtime() is None


def test_preflight_runtime_message_when_docker_down(monkeypatch):
    monkeypatch.setattr(sp, "docker_available", lambda **kw: False)
    msg = sp.preflight_runtime()
    assert msg is not None and "sandbox unavailable" in msg.lower()


def test_preflight_runtime_never_touches_auth(monkeypatch):
    # The whole point of the split: the runtime-only half must not import/call
    # anything operator-shaped, so `eval`/`submit` never risk a login prompt.
    monkeypatch.setattr(sp, "docker_available", lambda **kw: True)
    called = {"n": 0}

    def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("preflight_runtime must never check operator auth")
    monkeypatch.setattr(sp, "auth_docker_args", _boom)
    assert sp.preflight_runtime() is None
    assert called["n"] == 0


def test_preflight_operator_none_when_auth_ok(monkeypatch):
    monkeypatch.setattr(sp, "auth_docker_args", lambda *a, **kw: [])
    assert sp.preflight_operator("codex", system="Linux", home=Path("/h")) is None


def test_preflight_operator_message_when_auth_unavailable(monkeypatch):
    def _raise(operator, **kw):
        raise sp.AuthUnavailable(operator, "run `codex login` on this host, then retry")
    monkeypatch.setattr(sp, "auth_docker_args", _raise)
    msg = sp.preflight_operator("codex", system="Linux", home=Path("/h"))
    assert msg is not None and "not logged in" in msg.lower() and "codex login" in msg


def test_combined_preflight_still_checks_both_for_backward_compat(monkeypatch):
    monkeypatch.setattr(sp, "docker_available", lambda **kw: True)
    monkeypatch.setattr(sp, "auth_docker_args", lambda *a, **kw: [])
    assert sp.preflight("codex", system="Linux", home=Path("/h")) is None

    def _raise(operator, **kw):
        raise sp.AuthUnavailable(operator, "run `codex login`")
    monkeypatch.setattr(sp, "auth_docker_args", _raise)
    msg = sp.preflight("codex", system="Linux", home=Path("/h"))
    assert msg is not None and "not logged in" in msg.lower()


# --- the eval/submit CLI path: preflight_runtime only, NEVER preflight_operator


def test_eval_path_calls_preflight_runtime_but_never_preflight_operator(
        monkeypatch, tmp_path, capsys):
    """A plain `eval` scores in the arena sandbox, which has no operator --
    it must never resolve/require a codex or claude login (spec S5.5)."""
    from nethackers import cli

    calls = {"runtime": 0, "operator": 0}
    monkeypatch.setattr(cli, "preflight_runtime", lambda **kw: calls.update(
        runtime=calls["runtime"] + 1) or None)
    monkeypatch.setattr(cli, "ensure_image", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "container_runtime", lambda **kw: "docker")
    # Even if something regressed and reached real auth resolution, it must
    # not be reachable from here -- assert on the real chokepoint too.
    monkeypatch.setattr(sp, "preflight_operator", lambda *a, **kw: calls.update(
        operator=calls["operator"] + 1) or None)
    monkeypatch.setattr(sp, "auth_docker_args", lambda *a, **kw: calls.update(
        operator=calls["operator"] + 1) or [])

    def fake_eval_batch(solution, spec, image, *, now, runtime="docker", max_parallel_evals=8):
        from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
        result = TrajectoryResult(0, "completed", 0.1, False, 1, 1, 1, None, None, 0.0)
        objective = Objective(character=None, seed_set=spec.name)
        return Evidence.from_results(
            solution_digest="sha256:z", objective=objective, evaluator_image=image,
            results=[result], created_at=now,
        )
    monkeypatch.setattr(cli, "eval_batch", fake_eval_batch)

    rc = cli.main(["eval", str(tmp_path), "--objective", "val-dwa-law-fem"])

    assert rc == 0
    assert calls["runtime"] == 1        # the runtime gate ran
    assert calls["operator"] == 0       # the operator gate never did
    assert "Traceback" not in capsys.readouterr().err


def test_eval_blocked_by_preflight_runtime_never_reaches_eval_batch(monkeypatch, tmp_path):
    from nethackers import cli

    monkeypatch.setattr(cli, "container_runtime", lambda **kw: None)
    monkeypatch.setattr(
        cli, "preflight_runtime",
        lambda **kw: "[red]sandbox unavailable[/]: no working container runtime found "
                     "— start colima, then retry")

    def _boom(*a, **kw):
        raise AssertionError("eval_batch must not run when preflight_runtime fails")
    monkeypatch.setattr(cli, "eval_batch", _boom)

    rc = cli.main(["eval", str(tmp_path), "--objective", "val-dwa-law-fem"])

    assert rc != 0


# --- a checkout's fingerprint ref: pull CI's image, else build on the pinned base ---

from nethackers import _image_pins  # noqa: E402

FP = "nethackers/mutator:h-" + "e" * 64
REMOTE = "ghcr.io/dunnolab/nethackers-mutator:h-" + "e" * 64


class _Result:
    def __init__(self, returncode, stdout=""):
        self.returncode, self.stdout = returncode, stdout


class _FakeDocker:
    """A scripted ``run`` answering the calls the fingerprint path makes, and
    recording every argv."""

    def __init__(self, *, remote_exists, local_refs=(), created=None):
        self.calls = []
        self.remote_exists = remote_exists
        self.local_refs = list(local_refs)
        self.created = created or {}

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        sub = argv[1:]
        if sub[:2] == ["image", "inspect"] and "--format" not in sub:
            return _Result(1)                          # the fingerprint ref isn't local yet
        if sub[:2] == ["manifest", "inspect"]:
            return _Result(0 if self.remote_exists else 1)
        if sub[:2] == ["image", "ls"]:
            return _Result(0, "\n".join(self.local_refs) + "\n")
        if sub[:2] == ["image", "inspect"]:
            refs = sub[sub.index("--format") + 2:]
            return _Result(0, "\n".join(self.created[r] for r in refs) + "\n")
        return _Result(0)                              # tag / image rm


def test_fingerprint_ref_pulls_the_ci_image_and_tags_it(tmp_path):
    docker = _FakeDocker(remote_exists=True, local_refs=[FP])
    popened = []

    def _popen(argv, **kw):
        popened.append(argv)
        return _FakeProc([], 0)

    err = sp.ensure_image(FP, "mutator", run=docker, popen=_popen, repo_root=lambda: tmp_path)

    assert err is None
    assert popened == [["docker", "pull", REMOTE]]      # pulled, never built
    assert ["docker", "tag", REMOTE, FP] in docker.calls


def test_fingerprint_ref_builds_on_the_pinned_base_when_ci_has_none(tmp_path):
    docker = _FakeDocker(remote_exists=False, local_refs=[FP])
    seen = {}

    def _popen(argv, **kw):
        seen["argv"], seen["cwd"] = argv, kw.get("cwd")
        return _FakeProc(["#1 [internal] load build definition"], 0)

    err = sp.ensure_image(FP, "mutator", run=docker, popen=_popen, repo_root=lambda: tmp_path)

    assert err is None
    assert seen["argv"] == [
        "docker", "build", "-f", "Dockerfile.mutator",
        "--build-arg", f"NLE_BASE={_image_pins.NLE_BASE_IMAGE}",
        "--label", "org.dunnolab.nethackers.image=mutator",
        "-t", FP, ".",
    ]
    assert seen["cwd"] == str(tmp_path)


def test_fingerprint_build_failure_is_styled(tmp_path):
    docker = _FakeDocker(remote_exists=False)
    err = sp.ensure_image(FP, "mutator", run=docker, popen=lambda *a, **k: _FakeProc([], 1),
                          repo_root=lambda: tmp_path)
    assert err is not None and "setup failed" in err.lower() and "make" not in err


def test_cleanup_keeps_the_new_image_and_the_newest_other(tmp_path):
    old1, old2, newer = ("nethackers/mutator:h-" + c * 64 for c in "123")
    docker = _FakeDocker(
        remote_exists=True,
        local_refs=[old1, FP, newer, old2, "nethackers/mutator:latest"],
        created={old1: "2026-09-01T00:00:00Z", old2: "2026-09-02T00:00:00Z",
                 newer: "2026-09-10T00:00:00Z"},
    )
    sp.ensure_image(FP, "mutator", run=docker, popen=lambda *a, **k: _FakeProc([], 0),
                    repo_root=lambda: tmp_path)

    removed = sorted(c[3] for c in docker.calls if c[1:3] == ["image", "rm"])
    assert removed == sorted([
        old1, old2,
        "ghcr.io/dunnolab/nethackers-mutator:h-" + "1" * 64,
        "ghcr.io/dunnolab/nethackers-mutator:h-" + "2" * 64,
    ])
