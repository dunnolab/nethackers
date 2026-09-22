# tests/test_container_operator.py
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from nethackers.harness.auth_inject import AuthUnavailable
from nethackers.harness.container_operator import (
    ContainerCaps,
    ContainerOperator,
    _bridge_gateway_ip,
    build_docker_argv,
)
from nethackers.harness.cred_broker import HeaderRewrite


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
    assert "--standalone" not in a  # dropped in opencode-ai@1.x
    # effort rides a dedicated --variant flag now, not a `model#variant` suffix
    assert a[a.index("--model") + 1] == "gpt-x"
    assert a[a.index("--variant") + 1] == "high"


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


# --- selectable credential broker path (§3d, INV2) -------------------------
#
# `broker=True` swaps the credential MOUNT for base-URL env args pointing at
# a `CredBroker` this module starts/stops around the container's lifetime.
# Both the broker factory and the host-side credential read are injected
# fakes -- no real server, no real Keychain/`.codex`/`.credentials.json`
# touched by these tests.

@pytest.fixture(autouse=True)
def _stub_bridge_gateway(monkeypatch):
    # The broker's Linux bind-host lookup shells out to `docker network
    # inspect bridge` (these tests use system="Linux"); stub it so the unit
    # tier never touches real docker and the Linux broker path binds the
    # docker0 gateway deterministically.
    monkeypatch.setattr(
        "nethackers.harness.container_operator._bridge_gateway_ip",
        lambda *a, **k: "172.17.0.1",
    )


class _FakeBroker:
    """Stands in for `cred_broker.CredBroker`: records ctor args, fakes
    start()/stop() so no real socket/thread is ever created in a test.
    `port` defaults to the single-broker tests' existing hardcoded 9999; the
    multi-broker (opencode2) tests further down pass a distinct one per
    instance so two concurrently-"started" fakes are tellable apart.
    `impersonate` (default False, matching the real `CredBroker`) is recorded
    so a test can assert which harness's broker got constructed with it."""

    def __init__(self, upstream_base, rewrite, port=9999, impersonate=False, bind_host="127.0.0.1"):
        self.upstream_base = upstream_base
        self.rewrite = rewrite
        self.port = port
        self.impersonate = impersonate
        self.bind_host = bind_host
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.stopped = True


def _write_codex_cage_source(home: Path, account_id="acct-1"):
    """A minimal host `~/.codex/auth.json` the codex broker cage login reads
    its (non-secret) account_id out of. Real-looking tokens are present so a
    test can assert they never cross into the container's argv."""
    codex = home / ".codex"
    codex.mkdir(parents=True, exist_ok=True)
    (codex / "auth.json").write_text(json.dumps({
        "OPENAI_API_KEY": "", "auth_mode": "chatgpt",
        "tokens": {"access_token": "REAL-OAUTH", "refresh_token": "REAL-REF",
                   "account_id": account_id, "id_token": "REAL-ID"},
    }))


def _broker_op(tmp_path, harness="claude", system="Linux", **kw):
    holder = {}

    def fake_factory(upstream_base, rewrite, impersonate=False, bind_host="127.0.0.1"):
        b = _FakeBroker(upstream_base, rewrite, impersonate=impersonate, bind_host=bind_host)
        holder["broker"] = b
        return b

    op = ContainerOperator(
        harness=harness, image="img:test", system=system, home=tmp_path,
        broker=True,
        cred_broker_factory=fake_factory,
        broker_credential=lambda *a, **kw: HeaderRewrite(
            inject=(("Authorization", "Bearer REAL-SECRET-VALUE"),)
        ),
        **kw,
    )
    return op, holder


def test_broker_path_claude_env_and_add_host_no_mount(tmp_path):
    seen = {}
    op, holder = _broker_op(tmp_path, harness="claude")
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)

    op.run(wt, "BRIEF-TEXT")

    cmd = seen["cmd"]
    joined = " ".join(cmd)
    assert "ANTHROPIC_BASE_URL=http://host.docker.internal:9999" in joined
    assert "CLAUDE_CODE_OAUTH_TOKEN=proxy-managed" in joined   # OAuth mode, not x-api-key
    assert "--add-host" in cmd
    assert cmd[cmd.index("--add-host") + 1] == "host.docker.internal:host-gateway"
    assert "REAL-SECRET-VALUE" not in joined   # real key never reaches argv
    # no credential mount -- the only -v left is the workspace mount
    v_values = [v for flag, v in zip(cmd, cmd[1:], strict=False) if flag == "-v"]
    assert v_values == [f"{wt}:/workspace"]
    assert holder["broker"].upstream_base == "https://api.anthropic.com"
    assert ("Authorization", "Bearer REAL-SECRET-VALUE") in holder["broker"].rewrite.inject
    assert holder["broker"].started is True
    assert holder["broker"].stopped is True
    # claude's upstream isn't Cloudflare-fronted -- stays on the plain httpx
    # forward (impersonate=False), unlike codex's below.
    assert holder["broker"].impersonate is False
    # native Linux: the broker binds the docker0 gateway (stubbed 172.17.0.1),
    # NOT 127.0.0.1 which is unreachable from a Linux container.
    assert holder["broker"].bind_host == "172.17.0.1"


def test_broker_path_codex_via_c_override_at_chatgpt_host_upstream(tmp_path):
    seen = {}
    op, holder = _broker_op(tmp_path, harness="codex")
    # a host ~/.codex login exists (the broker reads it host-side) -- present
    # here to prove its real tokens still never reach the container argv
    _write_codex_cage_source(tmp_path, account_id="acct-777")
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)

    op.run(wt, "BRIEF-TEXT")

    cmd = seen["cmd"]
    joined = " ".join(cmd)
    # broker forwards to the ChatGPT-subscription HOST only -- codex's `-c`
    # base_url adds /backend-api/codex, so `upstream + path` reconstructs the
    # full endpoint (doubling the prefix here would 404)
    assert holder["broker"].upstream_base == "https://chatgpt.com"
    # routed by `-c` INVOCATION overrides (survive --ignore-user-config), not a
    # cage config.toml or the OPENAI_BASE_URL env (chatgpt mode ignores it)
    assert "OPENAI_BASE_URL" not in joined
    assert "OPENAI_API_KEY=proxy-managed" not in joined
    assert "model_provider=nethackers-broker" in cmd
    provider_c = next(t for t in cmd if t.startswith("model_providers.nethackers-broker="))
    # broker_base is the host-gateway URL (_FakeBroker's 9999 via host.docker.internal)
    assert 'base_url = "http://host.docker.internal:9999/backend-api/codex"' in provider_c
    assert "supports_websockets = false" in provider_c
    # unauthenticated custom provider -> codex sends no auth; the broker injects it
    assert "requires_openai_auth" not in provider_c
    assert "--add-host" in cmd
    # writable cage dir + CODEX_HOME; no token/config crosses the boundary
    assert "CODEX_HOME=/home/agent/.codex" in cmd
    # real tokens never reach argv, nor the broker's held header value
    assert "REAL-OAUTH" not in joined
    assert "REAL-REF" not in joined
    assert "REAL-ID" not in joined
    assert "REAL-SECRET-VALUE" not in joined
    # workspace mount + the single writable cage DIR mount (not two :ro files:
    # codex must be able to write its app-server state into ~/.codex)
    v_values = [v for flag, v in zip(cmd, cmd[1:], strict=False) if flag == "-v"]
    assert f"{wt}:/workspace" in v_values
    assert any(v.endswith(":/home/agent/.codex") for v in v_values)
    assert not any(v.endswith(":/home/agent/.codex/auth.json:ro") for v in v_values)
    assert holder["broker"].started is True
    assert holder["broker"].stopped is True
    # codex's upstream (chatgpt.com) IS Cloudflare-fronted -- the only broker
    # ever constructed with impersonate=True (curl_cffi Chrome TLS forward).
    assert holder["broker"].impersonate is True


def test_broker_stopped_even_if_the_run_raises(tmp_path):
    op, holder = _broker_op(tmp_path, harness="codex")
    _write_codex_cage_source(tmp_path)   # so auth resolves; the raise is in _popen
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)

    def boom(cmd, **kw):
        raise RuntimeError("boom")
    op._popen = boom

    with pytest.raises(RuntimeError):
        op.run(wt, "BRIEF-TEXT")

    assert holder["broker"].started is True
    assert holder["broker"].stopped is True


def test_broker_off_by_default_keeps_the_mount_and_no_add_host(tmp_path):
    # Regression pin: constructing/running WITHOUT `broker=` at all must stay
    # byte-identical to the pre-broker mount path -- nothing above may change
    # default behavior.
    seen = {}
    op = ContainerOperator(harness="codex", image="img:test", system="Linux", home=tmp_path)
    (tmp_path / ".codex").mkdir()
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)

    op.run(wt, "BRIEF-TEXT")

    cmd = seen["cmd"]
    assert "--add-host" not in cmd
    assert f"{tmp_path}/.codex:/home/agent/.codex" in cmd
    assert "OPENAI_BASE_URL" not in " ".join(cmd)
    # the broker-only `-c` provider override must never leak onto the mount path
    assert "model_provider=nethackers-broker" not in cmd
    assert "CODEX_HOME=/home/agent/.codex" not in cmd


def test_broker_fails_loud_with_no_login_never_falls_back_to_mount(tmp_path):
    """B3 / fail-loud (design §3.4): when the broker path can't resolve a
    real credential for claude/codex -- no login on this host -- `run` must
    raise `AuthUnavailable` straight through `_start_broker_auth`, never
    silently swap to the credential-MOUNT path (`auth_docker_args`), which
    would remount the very secret the broker exists to keep host-side.
    `cred_broker_factory` is a fail-fast stub here: `broker_credential` is
    called BEFORE it in `_start_broker_auth`'s claude/codex branch, so a
    correct implementation never even reaches it. `_popen` is asserted
    unreached too -- docker must never be shelled out to for either harness."""
    def _no_login(*a, **kw):
        raise AuthUnavailable("claude", "run `claude setup-token`, then retry")

    def _boom_if_a_broker_starts(*a, **kw):
        pytest.fail("a broker was started despite no resolvable credential")

    popen_calls: list = []
    for harness in ("claude", "codex"):
        op = ContainerOperator(
            harness=harness, image="img:test", system="Linux", home=tmp_path,
            broker=True,
            cred_broker_factory=_boom_if_a_broker_starts,
            broker_credential=_no_login,
        )
        op._popen = lambda cmd, **kw: popen_calls.append(cmd) or FakePopen(cmd, **kw)
        wt = tmp_path / "work" / f"iter-{harness}"
        wt.mkdir(parents=True)

        with pytest.raises(AuthUnavailable):
            op.run(wt, "BRIEF-TEXT")

    assert popen_calls == []   # docker never even ran -- no mount, no container


# The other half of B3 -- opencode2's "no brokerable provider" fallback to the
# credential mount is INTENTIONAL (nothing sensitive crosses there -- it's
# OpenCode's own free-model path) and must survive Task 5 unchanged. Already
# covered by `test_broker_path_opencode2_falls_back_to_mount_when_nothing_is_
# brokerable` below (a real no-brokerable-provider config fixture, not a
# stubbed resolver) -- that test is left as-is and re-run as a regression
# check rather than duplicated here.


def test_build_docker_argv_extra_args_precede_the_image():
    a = _argv("codex", extra_args=["--add-host", "host.docker.internal:host-gateway"])
    assert "--add-host" in a
    assert a.index("--add-host") < a.index("nethackers/mutator:test")
    assert a[a.index("--add-host") + 1] == "host.docker.internal:host-gateway"


def test_build_docker_argv_no_extra_args_by_default():
    a = _argv("codex")
    assert "--add-host" not in a


# --- opencode2 multi-broker path (§3d, INV2) --------------------------------
#
# opencode2 is multi-provider: unlike claude/codex's single `broker_proc`,
# `broker=True` here starts one `CredBroker` PER BROKERABLE PROVIDER
# (`auth_inject.opencode2_broker_targets`) and rewrites the cage config to
# point each at its own broker (`opencode2_broker_docker_args`). A config
# with no brokerable provider falls back to the plain credential mount
# instead of starting zero brokers around an empty broker config.

def _opencode_global_config(home: Path, doc: dict, name: str = "opencode.json") -> None:
    path = home / ".config" / "opencode" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def _read_mounted_opencode_config(cmd: list[str], name: str = "opencode.json") -> dict:
    suffix = f":/home/agent/.config/opencode/{name}:ro"
    for flag, value in zip(cmd, cmd[1:], strict=False):
        if flag == "-v" and value.endswith(suffix):
            return json.loads(Path(value.removesuffix(suffix)).read_text())
    raise AssertionError(f"no opencode cage config mounted for {name} in {cmd}")


def _numbered_broker_factory(holder: list):
    """Like the single-broker tests' inline `fake_factory`, but for
    opencode2's N-brokers-per-run path: every call gets its own `_FakeBroker`
    on a distinct port (9001, 9002, ...) so a test can tell which broker
    served which provider, and appends each to `holder` -- there's no single
    `holder["broker"]` slot once a run can start more than one."""
    def factory(upstream_base, rewrite, impersonate=False, bind_host="127.0.0.1"):
        b = _FakeBroker(upstream_base, rewrite, port=9000 + len(holder) + 1,
                         impersonate=impersonate, bind_host=bind_host)
        holder.append(b)
        return b
    return factory


def test_broker_path_opencode2_starts_one_broker_per_brokerable_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("LEFTOVER_TOKEN", "leftover-value")
    _opencode_global_config(tmp_path, {
        "provider": {
            "anthropic": {"options": {"apiKey": "sk-ant-real"}},
            "custom": {"options": {"apiKey": "sk-custom-real",
                                   "baseURL": "https://api.custom.example/v1"}},
            # not brokerable (a {file:} key) but carries its own env ref --
            # exercises that non-brokered forwarding still works end-to-end
            # alongside two brokered providers in the same run.
            "leftover": {"options": {"apiKey": "{file:~/.secrets/k}"},
                        "env": ["LEFTOVER_TOKEN"]},
        },
    })
    brokers: list = []
    op = ContainerOperator(
        harness="opencode2", image="img:test", system="Linux", home=tmp_path,
        broker=True, cred_broker_factory=_numbered_broker_factory(brokers),
    )
    seen = {}
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)

    op.run(wt, "BRIEF-TEXT")

    assert len(brokers) == 2   # one per BROKERABLE provider -- "leftover" is not one
    by_upstream = {b.upstream_base: b for b in brokers}
    assert ("x-api-key", "sk-ant-real") in by_upstream["https://api.anthropic.com"].rewrite.inject
    assert ("Authorization", "Bearer sk-custom-real") in (
        by_upstream["https://api.custom.example/v1"].rewrite.inject
    )
    assert all(b.started for b in brokers)
    assert all(b.stopped for b in brokers)
    # opencode2's providers aren't Cloudflare-fronted the way codex's
    # chatgpt.com is -- every opencode2 broker stays impersonate=False.
    assert all(b.impersonate is False for b in brokers)

    cmd = seen["cmd"]
    joined = " ".join(cmd)
    assert "sk-ant-real" not in joined
    assert "sk-custom-real" not in joined
    assert cmd.count("--add-host") == 1     # one add-host, not one per broker
    assert "host.docker.internal:host-gateway" in cmd
    assert "LEFTOVER_TOKEN" in cmd          # non-brokered provider still forwarded by name
    assert "leftover-value" not in joined   # ...but only by name, never its value

    mounted = _read_mounted_opencode_config(cmd)
    anthropic_opts = mounted["provider"]["anthropic"]["options"]
    custom_opts = mounted["provider"]["custom"]["options"]
    assert anthropic_opts["apiKey"] == "proxy-managed"
    assert custom_opts["apiKey"] == "proxy-managed"
    assert anthropic_opts["baseURL"].startswith("http://host.docker.internal:")
    assert custom_opts["baseURL"].startswith("http://host.docker.internal:")
    assert anthropic_opts["baseURL"] != custom_opts["baseURL"]   # each got its OWN broker
    assert mounted["provider"]["leftover"]["options"]["apiKey"] == "{file:~/.secrets/k}"


def test_broker_path_opencode2_falls_back_to_mount_when_nothing_is_brokerable(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    _opencode_global_config(tmp_path, {
        "provider": {"custom": {"options": {"apiKey": "{file:~/.secrets/k}"}}},
    })
    brokers: list = []
    op = ContainerOperator(
        harness="opencode2", image="img:test", system="Linux", home=tmp_path,
        broker=True, cred_broker_factory=_numbered_broker_factory(brokers),
    )
    seen = {}
    op._popen = lambda cmd, **kw: seen.setdefault("cmd", cmd) and FakePopen(cmd, **kw)
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)

    op.run(wt, "BRIEF-TEXT")

    assert brokers == []                    # zero brokerable providers -- zero brokers started
    cmd = seen["cmd"]
    assert "--add-host" not in cmd
    mounted = _read_mounted_opencode_config(cmd)
    assert mounted == {"provider": {"custom": {"options": {"apiKey": "{file:~/.secrets/k}"}}}}


def test_broker_path_opencode2_stops_all_brokers_even_if_the_run_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    _opencode_global_config(tmp_path, {
        "provider": {
            "anthropic": {"options": {"apiKey": "sk-ant"}},
            "custom": {"options": {"apiKey": "sk-custom",
                                   "baseURL": "https://api.custom.example/v1"}},
        },
    })
    brokers: list = []
    op = ContainerOperator(
        harness="opencode2", image="img:test", system="Linux", home=tmp_path,
        broker=True, cred_broker_factory=_numbered_broker_factory(brokers),
    )

    def boom(cmd, **kw):
        raise RuntimeError("boom")
    op._popen = boom
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)

    with pytest.raises(RuntimeError):
        op.run(wt, "BRIEF-TEXT")

    assert len(brokers) == 2
    assert all(b.started and b.stopped for b in brokers)
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


def test_bridge_gateway_ip_reads_the_docker_bridge_gateway():
    def fake_run(cmd, **kw):
        assert cmd[0] == "docker" and cmd[1:3] == ["network", "inspect"]
        class R:
            stdout = "172.17.0.1\n"
        return R()
    assert _bridge_gateway_ip("docker", run=fake_run) == "172.17.0.1"


def test_bridge_gateway_ip_none_when_docker_unavailable():
    def boom(cmd, **kw):
        raise OSError("docker not found")
    assert _bridge_gateway_ip("docker", run=boom) is None


def test_bridge_gateway_ip_none_on_empty_output():
    def empty(cmd, **kw):
        class R:
            stdout = "\n"
        return R()
    assert _bridge_gateway_ip("docker", run=empty) is None


def test_broker_binds_loopback_on_darwin(tmp_path, monkeypatch):
    # macOS: Docker Desktop routes the container's host.docker.internal to the
    # host loopback, so the broker binds 127.0.0.1 and must NOT look up a
    # docker bridge gateway.
    monkeypatch.setattr(
        "nethackers.harness.container_operator._bridge_gateway_ip",
        lambda *a, **k: pytest.fail("must not look up the bridge gateway on Darwin"),
    )
    op, holder = _broker_op(tmp_path, harness="claude", system="Darwin")
    wt = tmp_path / "work" / "iter-1"
    wt.mkdir(parents=True)
    op._popen = lambda cmd, **kw: FakePopen(cmd, **kw)
    op.run(wt, "BRIEF")
    assert holder["broker"].bind_host == "127.0.0.1"
