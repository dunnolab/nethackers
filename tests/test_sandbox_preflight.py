# tests/test_sandbox_preflight.py
"""Unit tests for the mandatory-sandbox preflight (harness/sandbox_preflight.py):
the container-runtime check, and the None/message contract ``preflight`` returns
for the CLI and TUI to display. No real docker, network, or login is touched --
``shutil.which`` / the ``run`` callable / ``auth_docker_args`` are injected.
"""
import json
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from rich.markup import escape
from rich.text import Text

from nethackers.harness import sandbox_preflight as sp
from nethackers.harness.auth_inject import AuthUnavailable


class _Info:
    def __init__(self, returncode):
        self.returncode = returncode


# --- docker_available: delegates to container_runtime (docker OR podman) --
# so it's driven by patching the global `shutil.which` (what container_runtime
# probes) + the injected `run` (the `<exe> info` liveness call). "docker" here
# means a docker-compatible runtime, podman included (issue #50).


def test_docker_available_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert sp.docker_available() is False


def test_docker_available_false_when_info_returns_nonzero(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    assert sp.docker_available(run=lambda *a, **kw: _Info(1)) is False


def test_docker_available_true_when_info_ok(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    assert sp.docker_available(run=lambda *a, **kw: _Info(0)) is True


def test_docker_available_false_when_info_raises(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

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
    # points at the one command that brings a runtime up
    assert "nethackers setup" in low


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


def test_preflight_operator_opencode2_needs_no_key_and_writes_nothing(tmp_path, monkeypatch):
    # Free models run without a key, so opencode2 is always usable. The check
    # must not write the sandbox's provider-config copy: doctor is read-only.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    config = tmp_path / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"provider": {"p": {"options": {"apiKey": "k"}}}}')

    assert sp.preflight_operator("opencode2", system="Linux", home=tmp_path) is None
    assert not (tmp_path / ".nethackers").exists()


def test_preflight_does_not_probe_for_the_image(monkeypatch):
    # The image is auto-provisioned (ensure_image), NOT a precondition the
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


# --- the sandbox platform: the agent must measure the games the arena scores


def _inspecting(platforms, seen=None):
    """A fake ``run`` answering ``<runtime> image inspect ... <ref>`` from
    ``platforms`` (ref -> ``os/arch``; a ref missing from it isn't local)."""
    def _run(argv, **kw):
        if seen is not None:
            seen.append(argv)
        platform = platforms.get(argv[-1])
        if platform is None:
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout=platform + "\n")
    return _run


def test_image_platform_reads_the_local_images_os_and_architecture():
    seen = []
    run = _inspecting({"img": "linux/arm64"}, seen)
    assert sp.image_platform("img", runtime="podman", run=run) == "linux/arm64"
    assert seen[0][:3] == ["podman", "image", "inspect"]


def test_image_platform_is_none_when_the_image_cant_be_read():
    assert sp.image_platform("absent", run=_inspecting({})) is None

    def _raise(*a, **kw):
        raise OSError("no docker")
    assert sp.image_platform("img", run=_raise) is None


def test_image_platform_is_none_for_a_record_without_os_and_architecture():
    # An image record missing both fields renders as "/", which must not read as
    # a platform that differs from the other image's.
    assert sp.image_platform("img", run=_inspecting({"img": "/"})) is None
    run = _inspecting({"arena": "linux/amd64", "mut": "/"})
    assert sp.sandbox_platform_mismatch("arena", "mut", run=run) is None


def test_sandboxes_on_one_platform_pass():
    run = _inspecting({"arena": "linux/amd64", "mut": "linux/amd64"})
    assert sp.sandbox_platform_mismatch("arena", "mut", run=run) is None


def test_a_mutator_on_another_platform_is_refused():
    # The Apple Silicon failure: an arm64 mutator beside the amd64 arena. The
    # agent's own evaluations then play different dungeons than it is scored on.
    run = _inspecting({"arena": "linux/amd64", "my/mut:x": "linux/arm64"})
    msg = sp.sandbox_platform_mismatch("arena", "my/mut:x", run=run)
    assert msg is not None
    plain = Text.from_markup(msg).plain
    assert "linux/arm64" in plain and "linux/amd64" in plain
    # Rebuilds the refused ref itself: a bare `make mutator` would build
    # nethackers/mutator:latest and leave the refused one as it was.
    assert "`make mutator MUTATOR_IMAGE=my/mut:x`" in plain


def test_an_arena_on_another_platform_is_the_one_to_rebuild():
    # A local-stack arena built natively on Apple Silicon: the mutator is right.
    run = _inspecting({"nethackers/arena:wt": "linux/arm64", "mut": "linux/amd64"})
    msg = sp.sandbox_platform_mismatch("nethackers/arena:wt", "mut", run=run)
    assert msg is not None
    assert "`make arena ARENA_IMAGE=nethackers/arena:wt`" in Text.from_markup(msg).plain


def test_a_digest_override_on_another_platform_is_dropped_not_rebuilt():
    # A build can't produce bytes under someone else's digest.
    digest_ref = "ghcr.io/someone/mutator@sha256:" + "d" * 64
    run = _inspecting({"arena": "linux/amd64", digest_ref: "linux/arm64"})
    plain = Text.from_markup(sp.sandbox_platform_mismatch("arena", digest_ref, run=run)).plain
    assert "drop its image override" in plain and "make" not in plain


def test_our_own_mutator_on_another_platform_is_removed_to_be_fetched_again():
    # Our fingerprint tag is re-acquired for the reference platform once gone;
    # `make mutator` would build a different tag.
    fingerprint = "nethackers/mutator:h-" + "e" * 64
    run = _inspecting({"arena": "linux/amd64", fingerprint: "linux/arm64"})
    plain = Text.from_markup(
        sp.sandbox_platform_mismatch("arena", fingerprint, runtime="podman", run=run)).plain
    assert f"`podman image rm {fingerprint}`" in plain and "make" not in plain


def test_an_unreadable_platform_never_blocks_a_run():
    # Only a definite mismatch refuses: a failed inspect is not evidence of one.
    run = _inspecting({"arena": "linux/amd64"})          # the mutator can't be read
    assert sp.sandbox_platform_mismatch("arena", "mut", run=run) is None


def test_only_our_own_mutator_refs_run_with_the_platform_flag():
    from nethackers import _image_pins
    fingerprint = "nethackers/mutator:h-" + "e" * 64
    assert sp.mutator_platform_args(_image_pins.MUTATOR_IMAGE) == ["--platform", "linux/amd64"]
    assert sp.mutator_platform_args(fingerprint) == ["--platform", "linux/amd64"]
    # An override may be a local arm64-only build, and `docker run --platform
    # linux/amd64` fails outright on one rather than running it.
    assert sp.mutator_platform_args("nethackers/mutator:latest") == []


# --- a scripted Popen: the build and pull tests below read its lines and exit code


class _FakeProc:
    def __init__(self, lines, rc):
        self.stdout = iter(lines)
        self._rc = rc

    def wait(self):
        return self._rc


# --- _build_image / _run_build: the on_event bracket (spec S5.5), a failed build's output
# These call them directly, as ensure_image's two build branches (3 and 4) do.


def test_build_image_emits_start_then_done_bracket(monkeypatch, tmp_path):
    (tmp_path / "Dockerfile.mutator").write_text("x")
    (tmp_path / "Makefile").write_text("x")
    monkeypatch.setattr(sp.Path, "cwd", classmethod(lambda cls: tmp_path))
    events = []
    lines: list[str] = []

    err = sp._build_image(
        "my/mut:tag", "mutator", on_event=events.append, on_line=lines.append,
        popen=lambda *a, **k: _FakeProc(["step 1/10\n"], 0),
    )

    assert err is None
    assert lines == ["step 1/10"]            # each build line streams to on_line, rstripped
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
    assert "the mutator image build did not complete (exit code 2)" in err   # no output to show
    assert [e.phase for e in events] == ["start", "error"]
    assert events[-1].layers_total is None and events[-1].layers_complete is None
    assert events[-1].detail == ""


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


def test_run_build_failure_carries_the_builds_last_lines(tmp_path):
    # evolve, doctor --pull and the TUI pass on_event only, never on_line, so the
    # build's own output is the only place a failure's cause can come from.
    lines = [f"#9 build line {n:02d}\n" for n in range(1, 21)]
    lines[9] = "#5 [internal] load metadata for x\n"
    lines[14] = "[/nope]\n"
    lines.insert(17, "\n")                     # blank lines don't count toward the 15
    events = []

    err = sp._run_build(["docker", "build", "."], cwd=tmp_path, image="img", kind="mutator",
                        on_event=events.append, popen=lambda *a, **k: _FakeProc(lines, 1))

    assert err is not None and "sandbox setup failed" in err
    assert "#9 build line 20" in err                                   # the last line
    assert escape("#5 [internal] load metadata for x") in err
    assert escape("[/nope]") in err
    assert "see the log above" not in err
    assert "build line 01" not in err                                  # line 1 is past the 15
    rendered = Text.from_markup(err).plain                             # Rich reads it literally
    assert "#5 [internal] load metadata for x" in rendered and "[/nope]" in rendered
    last_15_raw = [ln.rstrip() for ln in lines if ln.strip()][-15:]
    assert events[-1].phase == "error"
    assert events[-1].detail == "\n".join(last_15_raw)


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


def test_manifest_layers_reads_docker_verbose_output():
    doc = {"Descriptor": {"platform": {"os": "linux", "architecture": "amd64"}},
           "SchemaV2Manifest": {"layers": [{"digest": "sha256:a", "size": 10},
                                           {"digest": "sha256:b", "size": 20}]}}
    run = lambda argv, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(doc))  # noqa: E731
    assert sp.manifest_layers("img@sha256:x", runtime="docker", run=run) == {"sha256:a": 10,
                                                                             "sha256:b": 20}


def test_manifest_layers_picks_amd64_from_an_index_and_gives_up_on_garbage():
    arm = {"Descriptor": {"platform": {"os": "linux", "architecture": "arm64"}},
           "OCIManifest": {"layers": [{"digest": "sha256:arm", "size": 1}]}}
    amd = {"Descriptor": {"platform": {"os": "linux", "architecture": "amd64"}},
           "OCIManifest": {"layers": [{"digest": "sha256:amd", "size": 2}]}}
    run = lambda argv, **kw: SimpleNamespace(returncode=0, stdout=json.dumps([arm, amd]))  # noqa: E731
    assert sp.manifest_layers("img", runtime="docker", run=run) == {"sha256:amd": 2}
    bad = lambda argv, **kw: SimpleNamespace(returncode=0, stdout="not json")  # noqa: E731
    assert sp.manifest_layers("img", runtime="docker", run=bad) is None
    failed = lambda argv, **kw: SimpleNamespace(returncode=1, stdout="")  # noqa: E731
    assert sp.manifest_layers("img", runtime="docker", run=failed) is None


def test_download_size_counts_shared_layers_once_and_skips_what_is_present():
    layers = {"arena": {"sha256:base": 400, "sha256:arena": 40},
              "mutator": {"sha256:base": 400, "sha256:node": 430}}
    run = lambda argv, **kw: SimpleNamespace(  # noqa: E731
        returncode=0, stdout=json.dumps({"SchemaV2Manifest": {"layers": [
            {"digest": d, "size": s} for d, s in layers[argv[-1]].items()]}}))
    assert sp.download_size(["arena", "mutator"], [], runtime="docker", run=run) == 870
    assert sp.download_size(["mutator"], ["arena"], runtime="docker", run=run) == 430


def test_a_real_pty_pull_reports_bytes(tmp_path):
    """A fake `docker` script that redraws its progress the way docker does on a
    terminal (\\r, cursor moves) -- run through a real pseudo-terminal."""
    script = tmp_path / "fakedocker"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "w = sys.stdout.write\n"
        "w('0a1b2c3d4e5f: Pulling fs layer\\n')\n"
        "w('\\x1b[1A\\x1b[2K\\r0a1b2c3d4e5f: Downloading [==>   ]  1MB/4MB\\r\\x1b[1B')\n"
        "w('\\x1b[1A\\x1b[2K\\r0a1b2c3d4e5f: Downloading [=====>]  4MB/4MB\\r\\x1b[1B')\n"
        "w('\\x1b[1A\\x1b[2K\\r0a1b2c3d4e5f: Pull complete\\r\\x1b[1B\\n')\n"
        "sys.stdout.flush()\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    events = []
    assert sp._pull_image("img@sha256:x", "arena", runtime=str(script), on_event=events.append,
                          tty=True) is None
    assert max(e.bytes_done or 0 for e in events) == 4_000_000
    assert events[-1].phase == "done" and events[-1].bytes_total == 4_000_000
