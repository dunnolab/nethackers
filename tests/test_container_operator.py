# tests/test_container_operator.py
import os
import subprocess
import threading
from pathlib import Path

import pytest

from nethackers.harness.container_operator import (
    ContainerCaps,
    ContainerOperator,
    build_docker_argv,
)


def _argv(harness, **kw):
    return build_docker_argv(
        harness=harness, image="nethackers/mutator:test", name="nethackers-mut-r-1",
        worktree=Path("/runs/r/work/iter-1"), cli=None, model="gpt-x", effort="high",
        caps=ContainerCaps(), auth_args=["-v", "/h/.codex:/home/agent/.codex"],
        brief="B", **kw)


def test_docker_run_shape_and_caps():
    a = _argv("codex")
    assert a[:3] == ["docker", "run", "--rm"]
    assert "--name" in a and "nethackers-mut-r-1" in a
    for cap in ("--pids-limit", "512", "--memory", "8g", "--cpus", "4"):
        assert cap in a
    assert a[a.index("--security-opt") + 1] == "no-new-privileges"
    # swap must be capped too, else a runaway reaches ~2x --memory via swap
    assert a[a.index("--memory-swap") + 1] == ContainerCaps().memory
    assert "/runs/r/work/iter-1:/workspace" in a
    assert a[a.index("-w") + 1] == "/workspace"
    assert "nethackers/mutator:test" in a
    # auth args are present and precede the image
    assert a.index("-v") < a.index("nethackers/mutator:test")
    # wall-clock timeout wraps the in-container command
    assert "timeout" in a and str(ContainerCaps().timeout_s) in a


def test_our_own_mutator_images_run_on_the_reference_platform():
    from nethackers import _image_pins
    for image in (_image_pins.MUTATOR_IMAGE, "nethackers/mutator:h-" + "e" * 64):
        a = build_docker_argv(
            harness="codex", image=image, name="nethackers-mut-r-1",
            worktree=Path("/runs/r/work/iter-1"), cli=None, model=None, effort=None,
            caps=ContainerCaps(), auth_args=[], brief="B")
        assert a[:4] == ["docker", "run", "--platform", "linux/amd64"], image


def test_an_image_override_runs_without_a_platform_flag():
    # It may be a local arm64-only build, which --platform linux/amd64 would
    # refuse to run at all.
    assert "--platform" not in _argv("codex")          # nethackers/mutator:test


def test_codex_in_cage_bypasses_its_own_sandbox():
    a = _argv("codex")
    assert "--dangerously-bypass-approvals-and-sandbox" in a
    assert "--approve-for-me" not in a           # replaced, not appended
    assert "exec" in a                            # still `codex exec`


def test_claude_in_cage_skips_permissions():
    a = _argv("claude")
    assert "--dangerously-skip-permissions" in a
    assert "-p" in a


def test_opencode2_in_cage_runs_json_and_auto_approves():
    a = _argv("opencode2")
    assert a[a.index("--format") + 1] == "json"
    assert "--thinking" in a
    assert "--auto" in a
    assert "--standalone" in a
    assert a[a.index("--model") + 1] == "gpt-x#high"


def test_opencode2_brief_goes_on_stdin_not_in_argv():
    # `opencode2 run` quotes a spaced argv message and escapes its inner
    # quotes, so the brief's JSON commands arrived broken. stdin is verbatim,
    # and docker only forwards it with -i.
    a = _argv("opencode2")
    image_at = a.index("nethackers/mutator:test")
    assert "-i" in a[:image_at]
    assert "B" not in a[image_at:]
    assert "-i" not in _argv("codex")[:image_at]


def test_run_opencode2_writes_the_brief_to_stdin(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    seen = {}

    class StdinPopen(FakePopen):
        def __init__(self, cmd, **kw):
            super().__init__(cmd, **kw)
            seen["cmd"], seen["stdin_kw"] = cmd, kw.get("stdin")
            self.stdin = self
            self.written = ""
            seen["proc"] = self

        def write(self, text):
            self.written += text

        def close(self):
            pass

    brief = 'Measure with --batch \'[[0,"hum-law-fem"]]\' and a "quote".'
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)
    op = ContainerOperator(harness="opencode2", image="img:test", system="Linux", home=tmp_path)
    op._popen = StdinPopen

    op.run(wt, brief)

    assert brief not in seen["cmd"]
    assert seen["stdin_kw"] == subprocess.PIPE
    assert seen["proc"].written == brief


def test_unknown_harness_raises():
    with pytest.raises(ValueError, match="unknown harness"):
        _argv("pi")


def test_build_docker_argv_mounts_refs_readonly(tmp_path):
    refs = tmp_path / "refs"
    a = _argv("codex", refs=refs)
    assert "-v" in a and f"{refs}:/refs:ro" in a


class _DiscardingStdin:
    def write(self, text): return len(text)
    def close(self): pass


class FakePopen:
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.stdout = iter(['{"type":"x"}\n'])
        # Like Popen: a stdin object only when the caller asked for a pipe.
        self.stdin = _DiscardingStdin() if kw.get("stdin") == subprocess.PIPE else None
        self.pid = 4321
        self.returncode = 0
    def poll(self): return 0
    def wait(self, timeout=None): return 0


def test_run_delegates_and_builds_docker_argv(monkeypatch, tmp_path):
    seen = {}
    op = ContainerOperator(harness="codex", image="img:test", system="Linux", home=tmp_path)
    (tmp_path / ".codex").mkdir()
    wt = tmp_path / "work" / "iter-3"
    wt.mkdir(parents=True)
    # NB: `and`, not `or` -- dict.setdefault returns the (truthy) cmd list
    # itself, so `or` would short-circuit and hand run_operator a bare list
    # instead of a FakePopen, breaking on the first `proc.stdout` access.
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)
    res = op.run(wt, "BRIEF-TEXT")
    assert seen["cmd"][:3] == ["docker", "run", "--rm"]
    assert "BRIEF-TEXT" in seen["cmd"]        # real brief threaded into the inner cmd
    assert res.backend == "codex"


def test_run_opencode2_worktree_config_cannot_pick_host_env_vars(monkeypatch, tmp_path):
    # The worktree is a copy of someone else's program: its opencode.json must
    # not choose which host secrets enter the networked container.
    seen = {}
    wt = tmp_path / "work" / "iter-3"
    wt.mkdir(parents=True)
    (wt / "opencode.json").write_text(
        '{"provider": {"custom": {"options": {"apiKey": "{env:AWS_SECRET_ACCESS_KEY}"}}}}'
    )
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    op = ContainerOperator(
        harness="opencode2", image="img:test", system="Linux", home=tmp_path,
        model="custom/my-model",
    )
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)

    res = op.run(wt, "BRIEF-TEXT")

    assert res.backend == "opencode2"
    env = [value for flag, value in zip(seen["cmd"], seen["cmd"][1:], strict=False)
           if flag == "-e"]
    assert env == ["OPENCODE_DISABLE_PROJECT_CONFIG=1"]
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "custom/my-model"


def test_stop_docker_kills_named_container(tmp_path):
    killed = []
    op = ContainerOperator(harness="codex", image="img:test", system="Linux", home=tmp_path)
    (tmp_path / ".codex").mkdir()
    op._docker_kill = lambda name: killed.append(name)
    # drive the stop-watcher directly (unit): a set event → docker kill invoked
    # (name is an arbitrary stand-in for whatever run() computed -- shaped
    # like the real nethackers-mut-${RUN_ID}-${ITER} scheme, not the old
    # worktree-parent-derived one, since _maybe_kill_on_stop just relays it
    # verbatim)
    stop = threading.Event()
    stop.set()
    op._maybe_kill_on_stop("nethackers-mut-r9-iter-9", stop)
    assert killed == ["nethackers-mut-r9-iter-9"]


class _GatedFakePopen:
    """Like FakePopen, but its stdout blocks until `release` fires -- so the
    background stop-watcher is guaranteed a chance to act BEFORE run_operator
    can finish. Without this gate, a real (instant) FakePopen would let
    run_operator complete before the watcher's first 0.1s poll tick, making
    a same-name assertion racy: it would pass or fail depending on thread
    scheduling, not on whether run() is actually correct. poll() must stay
    non-None (never "still running") so run_operator's OWN internal watcher
    never attempts a real os.killpg on this fake/unrelated pid."""

    def __init__(self, cmd, release, **kw):
        self.cmd = cmd
        self.pid = 4321
        self._release = release

    @property
    def stdout(self):
        self._release.wait(timeout=2)
        return iter(['{"type":"x"}\n'])

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_stop_kills_the_same_name_baked_into_the_docker_argv(tmp_path):
    """run() computes the container name once; a silent divergence between
    the --name it bakes into the docker argv and the name its stop-watcher
    docker-kills would have `docker kill` target the wrong (or a
    nonexistent) container, so stop would fail silently. Pin that both call
    sites see the exact same value out of a single run() invocation.

    `_docker_kill` may legitimately fire more than once here: the watcher
    calls it, which (via `release`) lets the gated stdout resolve and
    run_operator finish -- and since nothing clears `stop`, run()'s own
    final synchronous safety-net call (added for the watcher-race fix, see
    test_stop_reliably_kills_even_when_run_operators_watcher_wins_the_race)
    then fires too. That double-call is the documented, harmless,
    idempotent case -- this test only cares that every call, however many,
    named the exact same container."""
    seen = {}
    killed = []
    release = threading.Event()

    op = ContainerOperator(harness="codex", image="img:test", system="Linux",
                           home=tmp_path, run_id="r7")
    (tmp_path / ".codex").mkdir()
    wt = tmp_path / "work" / "iter-7"
    wt.mkdir(parents=True)

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        return _GatedFakePopen(cmd, release, **kw)
    op._popen = fake_popen

    def fake_docker_kill(name):
        killed.append(name)
        release.set()  # let the gated stdout resolve now that the kill fired
    op._docker_kill = fake_docker_kill

    stop = threading.Event()
    stop.set()
    op.run(wt, "BRIEF-TEXT", stop=stop)

    name_in_argv = seen["cmd"][seen["cmd"].index("--name") + 1]
    assert name_in_argv == "nethackers-mut-r7-iter-7"
    assert killed                        # _docker_kill fired at least once
    assert set(killed) == {name_in_argv}  # every call used the SAME name


def test_run_id_makes_container_name_unique_across_two_concurrent_runs(tmp_path):
    """Regression for the cross-run collision the final review caught: the
    container name used to be derived from `worktree.parent.name`, which is
    ALWAYS the literal "work" (worktree == <workdir>/runs/<rid>/work/iter-N),
    so two concurrent `evolve --sandbox` runs collided on the exact same
    `nethackers-mut-work-iter-0` name regardless of run id -- and a hard-stop's
    `docker kill <name>` could then target the WRONG run's live container.
    Spec §3.3 requires `nethackers-mut-${RUN_ID}-${ITER}`; pin that two
    ContainerOperators
    constructed with DIFFERENT run ids, driving the SAME iteration number
    (iter-0 -- the exact shape of the pre-fix collision), get DIFFERENT
    docker --name values.
    """
    (tmp_path / ".codex").mkdir()
    seen_a: dict = {}
    seen_b: dict = {}

    op_a = ContainerOperator(harness="codex", image="img:test", system="Linux",
                             home=tmp_path, run_id="run-aaa")
    op_b = ContainerOperator(harness="codex", image="img:test", system="Linux",
                             home=tmp_path, run_id="run-bbb")
    op_a._popen = lambda cmd, **kw: seen_a.setdefault("cmd", cmd) and FakePopen(cmd, **kw)
    op_b._popen = lambda cmd, **kw: seen_b.setdefault("cmd", cmd) and FakePopen(cmd, **kw)

    # Same iteration number under each run's own work dir -- the exact shape
    # of the pre-fix collision (both worktrees' parent is literally "work").
    wt_a = tmp_path / "run-aaa" / "work" / "iter-0"
    wt_b = tmp_path / "run-bbb" / "work" / "iter-0"
    wt_a.mkdir(parents=True)
    wt_b.mkdir(parents=True)

    op_a.run(wt_a, "BRIEF-A")
    op_b.run(wt_b, "BRIEF-B")

    name_a = seen_a["cmd"][seen_a["cmd"].index("--name") + 1]
    name_b = seen_b["cmd"][seen_b["cmd"].index("--name") + 1]

    assert name_a != name_b
    assert name_a == "nethackers-mut-run-aaa-iter-0"
    assert name_b == "nethackers-mut-run-bbb-iter-0"


class _StillRunningFakePopen:
    """Unlike FakePopen/_GatedFakePopen above (poll() always 0 -- "already
    exited"), this fake is genuinely still "running" (poll() stays None)
    until run_operator's OWN internal stop-watcher notices `stop` and
    attempts to kill it. stdout blocks until that real kill-attempt happens
    (detected via the os.getpgid hook the test installs), so run_operator's
    completion is genuinely CAUSED BY its own watcher acting first -- the
    exact interleaving the race below depends on, and the thing a
    poll()-always-0 fake structurally cannot exercise (its kill-branch is
    dead code, so it can never be the reason run_operator returns)."""

    def __init__(self, cmd, unblock, **kw):
        self.cmd = cmd
        self.pid = 4321
        self._unblock = unblock

    @property
    def stdout(self):
        self._unblock.wait(timeout=2)
        return iter(['{"type":"x"}\n'])

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


def test_stop_reliably_kills_even_when_run_operators_watcher_wins_the_race(
    tmp_path, monkeypatch,
):
    """Regression for a Critical race found in review (empirically ~27.5%
    failure across 40 trials on the pre-fix code): ContainerOperator's own
    stop-watcher and run_operator's internal one both poll the same `stop`,
    but against different `done` events. When run_operator's watcher wins --
    notices `stop` first and SIGKILLs the local docker-run *client* --
    run_operator returns fast, run()'s `finally: done.set()` fires, and OUR
    watcher's `done.wait()` returns via the "already set" path without ever
    reaching its own `if stop.is_set()` check below it -- so `_docker_kill`
    never runs, and the container is left running (SIGKILLing the client
    does not stop it).

    Reproducing this needs a fake process that is genuinely still "running"
    (poll() returns None) so run_operator's watcher's real kill-branch is
    reachable -- see _StillRunningFakePopen. Both watchers then poll on the
    *same* ~0.1s cadence, so which one notices `stop` first is a genuine
    thread-scheduling race, not something a single trial can reliably catch
    either way (empirically: 10/40 misses before the fix in this module's
    own experiment, 0/40 after, in line with the reviewer's ~27.5%). Running
    N independent trials and requiring ALL to succeed makes this a reliable
    regression gate: with the fix, the final synchronous call in run()'s
    `finally` is unconditional and independent of watcher timing, so success
    is deterministic (N/N, always); without it, at an empirical ~25-27%
    single-trial miss rate, the chance of all 20 trials happening to dodge
    the race is under 1%.
    """
    op = ContainerOperator(harness="codex", image="img:test", system="Linux", home=tmp_path)
    (tmp_path / ".codex").mkdir()
    wt = tmp_path / "work" / "iter-race"
    wt.mkdir(parents=True)

    real_getpgid = os.getpgid
    current_unblock: list[threading.Event] = []

    def fake_getpgid(pid):
        # Only the fake pid triggers the hook -- anything else (unrelated
        # calls from elsewhere) falls through to the real implementation.
        if pid == 4321 and current_unblock:
            current_unblock[0].set()
            # This is what run_operator's own `os.killpg(os.getpgid(pid),
            # SIGKILL)` call feeds into `killpg` -- raising here means
            # `killpg` is never reached at all, so no real signal is ever
            # sent (contextlib.suppress(Exception) in run_operator's
            # watcher swallows this).
            raise ProcessLookupError("fake pid -- never a real process")
        return real_getpgid(pid)

    monkeypatch.setattr(os, "getpgid", fake_getpgid)

    trials = 20
    results = []
    for _ in range(trials):
        unblock = threading.Event()
        current_unblock[:] = [unblock]
        killed = []

        def fake_popen(cmd, _unblock=unblock, **kw):
            return _StillRunningFakePopen(cmd, _unblock, **kw)
        op._popen = fake_popen
        op._docker_kill = lambda name, _killed=killed: _killed.append(name)

        stop = threading.Event()
        stop.set()
        op.run(wt, "BRIEF-TEXT", stop=stop)
        results.append(bool(killed))

    misses = results.count(False)
    assert misses == 0, (
        f"{misses}/{trials} trials never called _docker_kill -- the "
        "container would have been left running"
    )


# --- rootless-podman userns args (issue #54) --------------------------------
# `nonroot_userns_args` (containers.py) decides WHETHER these are needed by
# probing the runtime; build_docker_argv/ContainerOperator only carry them, so
# the argv builder stays pure (no subprocess) and unit-drivable.


def test_userns_args_default_to_nothing_so_the_argv_is_unchanged():
    # every host that works today (docker, rootful podman) must get a
    # byte-identical argv to before this option existed.
    assert _argv("codex") == _argv("codex", userns_args=[])
    assert "--userns=keep-id" not in _argv("codex")


def test_userns_args_are_spliced_in_before_the_image():
    a = _argv("codex", userns_args=["--userns=keep-id", "--user", "0"])
    assert "--userns=keep-id" in a
    assert a[a.index("--user") + 1] == "0"
    # must be `run` flags, not arguments to the in-cage command
    assert a.index("--userns=keep-id") < a.index("nethackers/mutator:test")


def test_operator_threads_its_userns_args_into_the_run_argv(tmp_path):
    seen = {}
    op = ContainerOperator(harness="codex", image="img:test", system="Linux",
                           home=tmp_path, userns_args=["--userns=keep-id", "--user", "0"])
    (tmp_path / ".codex").mkdir()
    wt = tmp_path / "work" / "iter-3"
    wt.mkdir(parents=True)
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)
    op.run(wt, "BRIEF-TEXT")
    assert "--userns=keep-id" in seen["cmd"]
