# tests/test_sandbox_preflight.py
"""Unit tests for the mandatory-sandbox preflight (harness/sandbox_preflight.py):
the container-runtime check, and the None/message contract ``preflight`` returns
for the CLI and TUI to display. No real docker, network, or login is touched --
``shutil.which`` / the ``run`` callable / ``auth_docker_args`` are injected.
"""
import threading
import time
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


# --- _build_image: on_event start/done/error bracket (spec S5.5) -----------
# build_mutator_image (the public wrapper exercised above) doesn't forward
# on_event -- these call _build_image directly, exactly like ensure_image's
# own branch 3 does.


def test_build_image_emits_start_then_done_bracket(monkeypatch, tmp_path):
    (tmp_path / "Dockerfile.mutator").write_text("x")
    (tmp_path / "Makefile").write_text("x")
    monkeypatch.setattr(sp.Path, "cwd", classmethod(lambda cls: tmp_path))
    events = []

    err = sp._build_image(
        "my/mut:tag", "mutator", on_event=events.append,
        popen=lambda *a, **k: _FakeProc(["step 1/10"], 0),
    )

    assert err is None
    assert [e.phase for e in events] == ["start", "done"]
    # No layer concept on the make path -- always None/None, never 0/0.
    assert all(e.layers_total is None and e.layers_complete is None for e in events)
    assert all(e.kind == "mutator" and e.ref == "my/mut:tag" for e in events)


def test_build_image_emits_error_event_on_build_failure(monkeypatch, tmp_path):
    (tmp_path / "Dockerfile.mutator").write_text("x")
    (tmp_path / "Makefile").write_text("x")
    monkeypatch.setattr(sp.Path, "cwd", classmethod(lambda cls: tmp_path))
    events = []

    err = sp._build_image("img", "mutator", on_event=events.append,
                          popen=lambda *a, **k: _FakeProc([], 2))

    assert err is not None and "setup failed" in err.lower()
    assert [e.phase for e in events] == ["start", "error"]
    assert events[-1].layers_total is None and events[-1].layers_complete is None


def test_build_image_emits_no_events_when_outside_the_repo(monkeypatch, tmp_path):
    # The "no repo checkout" early-return (sandbox_preflight.py's
    # _build_image, before the on_event(start) call) fires before the
    # bracket even opens -- zero events, not a dangling "start" with no
    # matching terminal event.
    monkeypatch.setattr(sp.Path, "cwd", classmethod(lambda cls: tmp_path))
    events = []

    err = sp._build_image("img", "mutator", on_event=events.append,
                          popen=lambda *a, **k: _FakeProc([], 0))

    assert err is not None and "repo" in err.lower()
    assert events == []


# --- _pull_image: typed PullEvents alongside the raw on_line (spec S5.5) ----


def test_pull_image_emits_typed_events_and_still_calls_on_line():
    lines = [
        "1b930d010525: Pulling fs layer\n",
        "1b930d010525: Pull complete\n",
        "Status: Downloaded newer image for img:tag\n",
    ]
    on_lines: list[str] = []
    on_events = []

    err = sp._pull_image(
        "img:tag", "arena",
        on_line=on_lines.append,
        on_event=on_events.append,
        popen=lambda *a, **k: _FakeProc(lines, 0),
    )

    assert err is None
    # on_line still gets every raw (rstripped) line -- no caller is forced to
    # migrate off it.
    assert on_lines == [
        "1b930d010525: Pulling fs layer",
        "1b930d010525: Pull complete",
        "Status: Downloaded newer image for img:tag",
    ]
    phases = [e.phase for e in on_events]
    assert phases[0] == "start"
    assert "layer" in phases
    assert phases[-1] == "done"  # the final bracketing event _pull_image adds itself
    # Fix round 1: the Status: line is noise at the parser level now, so this
    # final bracket is the ONLY "done" -- a successful pull must never
    # double-fire it (it used to, once from parse_pull_line's Status: line
    # and once from this bracket).
    assert phases.count("done") == 1
    layer_events = [e for e in on_events if e.phase == "layer"]
    assert layer_events[-1].layers_total == 1
    assert layer_events[-1].layers_complete == 1
    for e in on_events:
        assert e.kind == "arena" and e.ref == "img:tag"


def test_pull_image_emits_error_event_on_registry_failure():
    on_events = []
    err = sp._pull_image(
        "img:tag", "arena",
        on_event=on_events.append,
        popen=lambda *a, **k: _FakeProc(
            ["denied: requested access to the resource is denied\n"], 1,
        ),
    )
    assert err is not None and "stale ghcr login" in err.lower()
    phases = [e.phase for e in on_events]
    assert phases[0] == "start"
    assert phases[-1] == "error"
    assert phases.count("error") == 1  # exactly one error, not one per line
    # No layer line was ever seen in this transcript -- None, not 0 (same
    # None-not-zero convention as parse_pull_line's own "nothing seen" case).
    assert on_events[-1].layers_total is None
    assert on_events[-1].layers_complete is None


def test_pull_image_emits_error_event_when_popen_raises():
    # The except (OSError, subprocess.SubprocessError) branch: popen() itself
    # blows up before a single line is read. Exactly one "error" event still
    # reaches on_event (state is initialized before the try, so there's no
    # UnboundLocalError risk), and the function still returns a mapped,
    # styled message rather than letting the exception propagate.
    def _raising_popen(*a, **k):
        raise OSError("boom")

    on_events = []
    err = sp._pull_image("img:tag", "arena", on_event=on_events.append, popen=_raising_popen)

    assert err is not None and "couldn't reach the registry" in err.lower()
    phases = [e.phase for e in on_events]
    assert phases == ["start", "error"]
    assert on_events[-1].layers_total is None
    assert on_events[-1].layers_complete is None
    assert on_events[-1].detail == "boom"


def test_pull_image_works_with_on_event_omitted():
    # Existing callers that only pass on_line must be completely unaffected.
    err = sp._pull_image(
        "img:tag", "arena",
        on_line=lambda ln: None,
        popen=lambda *a, **k: _FakeProc(["1b930d010525: Pull complete\n"], 0),
    )
    assert err is None


# --- ensure_image: in-process leader/follower dedup for the same ref -------


def test_ensure_image_dedups_concurrent_pulls_for_the_same_ref():
    # Two threads racing ensure_image() for the SAME absent ref must trigger
    # exactly one underlying pull -- the second waits instead of starting its
    # own. The fake "pull" sleeps briefly so the second thread reliably
    # arrives while the first is still in flight (generous margin -- real
    # thread-start latency is microseconds, not the 150ms slept here).
    pull_calls = {"n": 0}
    calls_lock = threading.Lock()
    pull_finished = threading.Event()

    def _fake_popen(argv, **kw):
        with calls_lock:
            pull_calls["n"] += 1
        time.sleep(0.15)
        pull_finished.set()
        return _FakeProc(["Status: Downloaded newer image for img:tag\n"], 0)

    def _fake_run(*a, **k):
        return SimpleNamespace(returncode=0 if pull_finished.is_set() else 1)

    ref = "ghcr.io/x/y@sha256:" + "a" * 64
    results: list[str | None] = []
    results_lock = threading.Lock()

    def _call():
        r = sp.ensure_image(ref, "arena", popen=_fake_popen, run=_fake_run)
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert pull_calls["n"] == 1
    assert results == [None, None]


def test_ensure_image_follower_rechecks_rather_than_assumes_leader_succeeded():
    # If the leader's pull FAILS, a waiting follower must not assume success
    # once woken -- it re-checks image_present (still absent here), finds it
    # missing, and retries as a new leader itself. The meaningful assertion
    # is on `results`, not just the call count: a buggy implementation that
    # has the follower return None unconditionally after waking would still
    # produce *some* call count here, but would wrongly report success for
    # one of the two callers.
    pull_calls = {"n": 0}
    calls_lock = threading.Lock()

    def _fake_popen(argv, **kw):
        with calls_lock:
            pull_calls["n"] += 1
        time.sleep(0.15)
        return _FakeProc(["denied: requested access to the resource is denied\n"], 1)

    ref = "ghcr.io/x/y@sha256:" + "b" * 64
    results: list[str | None] = []
    results_lock = threading.Lock()

    def _call():
        r = sp.ensure_image(ref, "arena", popen=_fake_popen,
                            run=lambda *a, **k: SimpleNamespace(returncode=1))
        with results_lock:
            results.append(r)

    threads = [threading.Thread(target=_call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert pull_calls["n"] == 2  # leader attempt + the retrying follower's own attempt
    assert len(results) == 2
    assert all(r is not None and "stale ghcr login" in r.lower() for r in results)


def test_ensure_image_dedup_registry_is_cleaned_up_after_completion():
    # No leaked entry in the module-level in-flight registry once a pull
    # (successful or not) has finished -- a later, unrelated call for the
    # same ref must not find a stale entry and wait forever.
    ref = "ghcr.io/x/y@sha256:" + "c" * 64
    sp.ensure_image(
        ref, "arena",
        popen=lambda *a, **k: _FakeProc(["Status: Downloaded newer image for img\n"], 0),
        run=lambda *a, **k: SimpleNamespace(returncode=1),
    )
    assert ref not in sp._inflight_pulls
