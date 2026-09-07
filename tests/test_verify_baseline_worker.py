"""Tests for the worker's ``--baseline`` mode: computing AutoAscend's
hidden-seed floor and submitting it per identity.

Unlike ``verify_program`` (which pulls a participant's repo), the floor is
computed from a local AutoAscend tree the operator points at -- there is no
``repo@commit`` for it, and it must never acquire one. The behaviour that
earns its own tests here is RESUME: the run is ~1095 episodes and hours long,
so dying at identity 50 must not restart from zero.
"""

from __future__ import annotations

import pytest

from nethackers._image_pins import ARENA_IMAGE
from nethackers.arena.seeds import secret_fingerprint
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.eval.runner import DEFAULT_MAX_PARALLEL_EVALS
from nethackers.worker.verify import compute_hidden_baseline

CONFIG = {"secret": "hidden-key", "seeds": [4839201, 1029384]}
IDENTS = ("val-dwa-law-fem", "wiz-elf-cha-mal")


def _evidence(spec):
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=0.4, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character=char, milestone=None)
        for sd, char in spec.batch)
    return Evidence.from_results(
        solution_digest="autoascend", objective=Objective(character=None, seed_set="v"),
        evaluator_image=ARENA_IMAGE, results=results, created_at="t")


class _Client:
    """A hub that records baseline submissions and reports coverage back the
    way the real ``GET /verify/overview`` does."""

    def __init__(self, covered: dict[str, int] | None = None):
        self.submitted: list[dict] = []
        self._covered = covered or {}

    def get_verify_overview(self):
        return {"per_identity": {}, "overall": None,
                "baseline": {"per_identity": {i: {"episodes": n}
                                              for i, n in self._covered.items()},
                             "overall": None}}

    def post_verify_baseline(self, token, *, evidence, secret_fingerprint):
        self.submitted.append(evidence)
        return {"inserted": len(evidence["results"]),
                "coverage": {"done": len(evidence["results"]), "total": 2}}


def _run(client, *, eval_fn=None, identities=IDENTS, tree="roots/autoascend"):
    return compute_hidden_baseline(
        client, "vt", CONFIG, tree,
        eval_fn=eval_fn or (lambda tree, spec, image, *, now, secret, **kw: _evidence(spec)),
        now_fn=lambda: "t", identities=identities)


def test_submits_one_batch_per_identity_on_the_hidden_seeds():
    client = _Client()
    assert _run(client) == "succeeded"
    assert len(client.submitted) == 2
    submitted_seeds = {r["trajectory_id"] for ev in client.submitted for r in ev["results"]}
    assert submitted_seeds == set(CONFIG["seeds"])


def test_passes_the_hidden_secret_to_the_evaluator_not_the_seeds_alone():
    """The arena needs the secret to derive the real seeds; a run that
    forgot it would silently score public seeds and look plausible."""
    seen = {}

    def spy(tree, spec, image, *, now, secret, **kw):
        seen["secret"] = secret
        seen["image"] = image
        return _evidence(spec)

    _run(_Client(), eval_fn=spy, identities=("val-dwa-law-fem",))
    assert seen["secret"] == CONFIG["secret"]
    assert seen["image"] == ARENA_IMAGE


def test_skips_identities_already_complete_for_this_epoch():
    """Resume: a 3-hour run that died partway must not recompute what the
    hub already holds."""
    client = _Client(covered={"val-dwa-law-fem": len(CONFIG["seeds"])})
    assert _run(client) == "succeeded"
    assert len(client.submitted) == 1
    assert client.submitted[0]["results"][0]["character"] == "wiz-elf-cha-mal"


def test_recomputes_a_partially_covered_identity():
    """Partial coverage is not coverage -- an identity with some seeds
    missing must be redone, not skipped."""
    client = _Client(covered={"val-dwa-law-fem": 1})
    assert _run(client) == "succeeded"
    assert len(client.submitted) == 2


def test_reports_failed_when_the_evaluator_crashes():
    def boom(*a, **k):
        raise RuntimeError("container died")

    assert _run(_Client(), eval_fn=boom) == "failed"


def test_a_crash_partway_keeps_the_identities_already_submitted():
    """Each identity is submitted as it finishes, so a later crash never
    discards hours of completed work."""
    calls = []

    def flaky(tree, spec, image, *, now, secret, **kw):
        calls.append(spec)
        if len(calls) == 2:
            raise RuntimeError("container died")
        return _evidence(spec)

    client = _Client()
    assert _run(client, eval_fn=flaky) == "failed"
    assert len(client.submitted) == 1


def test_secret_fingerprint_is_sent_never_the_raw_secret():
    sent = {}

    class _C(_Client):
        def post_verify_baseline(self, token, *, evidence, secret_fingerprint):
            sent["fp"] = secret_fingerprint
            return super().post_verify_baseline(
                token, evidence=evidence, secret_fingerprint=secret_fingerprint)

    _run(_C(), identities=("val-dwa-law-fem",))
    assert sent["fp"] == secret_fingerprint(CONFIG["secret"])
    assert sent["fp"] != CONFIG["secret"]


def test_rejects_an_identity_that_is_not_a_real_identity():
    """A typo'd --identity must fail loudly here rather than produce a
    partial floor the hub silently accepts."""
    with pytest.raises(KeyError):
        _run(_Client(), identities=("not-an-identity",))


# ---------------------------------------------------------------------------
# CLI wiring: `nethackers-worker --baseline`
# ---------------------------------------------------------------------------

import nethackers.worker.server as server  # noqa: E402


class _StubHub:
    def __init__(self, *a, **k): pass
    def get_verify_config(self, token): return {"secret": "s", "seeds": [1]}
    def get_verify_candidates(self, token, *, limit=8):
        raise AssertionError("--baseline must not pull the participant work queue")


def _stub_hub(monkeypatch):
    monkeypatch.setattr(server, "HubClient", _StubHub)


def test_baseline_flag_computes_the_floor_and_exits_zero(monkeypatch):
    seen = {}

    def _fake(client, token, config, tree, **kw):
        seen["tree"] = tree
        return "succeeded"

    _stub_hub(monkeypatch)
    monkeypatch.setattr(server, "compute_hidden_baseline", _fake)
    with pytest.raises(SystemExit) as exc:
        server.main(["--hub", "http://h", "--token", "vt", "--baseline"])
    assert exc.value.code == 0
    assert seen["tree"] == "roots/autoascend"  # documented default


def test_baseline_tree_is_overridable(monkeypatch):
    seen = {}
    _stub_hub(monkeypatch)
    def _fake(client, token, config, tree, **kw):
        seen["tree"] = tree
        return "succeeded"

    monkeypatch.setattr(server, "compute_hidden_baseline", _fake)
    with pytest.raises(SystemExit):
        server.main(["--hub", "http://h", "--token", "vt",
                     "--baseline", "--tree", "/srv/autoascend"])
    assert seen["tree"] == "/srv/autoascend"


def test_baseline_exits_nonzero_on_failure(monkeypatch):
    """Exit code = outcome, so a systemd unit or a shell loop can tell a
    partial floor from a complete one."""
    _stub_hub(monkeypatch)
    monkeypatch.setattr(server, "compute_hidden_baseline", lambda *a, **k: "failed")
    with pytest.raises(SystemExit) as exc:
        server.main(["--hub", "http://h", "--token", "vt", "--baseline"])
    assert exc.value.code != 0


def test_baseline_never_enters_the_participant_daemon_loop(monkeypatch):
    """--baseline is a one-shot: it must not fall through into the candidate
    polling loop (the _StubHub raises if the queue is fetched)."""
    _stub_hub(monkeypatch)
    monkeypatch.setattr(server, "compute_hidden_baseline", lambda *a, **k: "succeeded")
    monkeypatch.setattr(server, "verify_program",
                        lambda *a, **k: pytest.fail("--baseline must not verify programs"))
    with pytest.raises(SystemExit) as exc:
        server.main(["--hub", "http://h", "--token", "vt", "--baseline"])
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Parallelism: the arena caps concurrent episodes at
# min(max_parallel_evals, len(batch)) worker PROCESSES, so this is effectively
# "how many cores to use". It must be settable per box -- the eval node has 4
# CPUs, a dev Mac has 16 -- and it must actually reach eval_batch rather than
# silently inheriting a default.
# ---------------------------------------------------------------------------


def test_baseline_defaults_to_the_shared_default_parallelism():
    seen = {}

    def spy(tree, spec, image, *, now, secret, max_parallel_evals):
        seen["n"] = max_parallel_evals
        return _evidence(spec)

    _run(_Client(), eval_fn=spy, identities=("val-dwa-law-fem",))
    assert seen["n"] == DEFAULT_MAX_PARALLEL_EVALS


def test_baseline_parallelism_reaches_the_evaluator():
    seen = {}

    def spy(tree, spec, image, *, now, secret, max_parallel_evals):
        seen["n"] = max_parallel_evals
        return _evidence(spec)

    compute_hidden_baseline(_Client(), "vt", CONFIG, "roots/autoascend", eval_fn=spy,
                            now_fn=lambda: "t", identities=("val-dwa-law-fem",),
                            log=lambda m: None, max_parallel_evals=15)
    assert seen["n"] == 15


def test_cli_parallelism_defaults_and_overrides(monkeypatch):
    seen = {}
    _stub_hub(monkeypatch)
    monkeypatch.setattr(server, "compute_hidden_baseline",
                        lambda *a, **kw: seen.update(kw) or "succeeded")
    with pytest.raises(SystemExit):
        server.main(["--hub", "http://h", "--token", "vt", "--baseline"])
    assert seen["max_parallel_evals"] == DEFAULT_MAX_PARALLEL_EVALS

    seen.clear()
    with pytest.raises(SystemExit):
        server.main(["--hub", "http://h", "--token", "vt", "--baseline",
                     "--max-parallel-evals", "15"])
    assert seen["max_parallel_evals"] == 15


def test_cli_parallelism_also_reaches_program_verification(monkeypatch):
    """The same flag must steer the participant path -- that is where the
    4-CPU eval box is currently oversubscribed at the default 8."""
    seen = {}

    class _Hub(_StubHub):
        def get_verify_candidates(self, token, *, limit=8):
            if getattr(self, "_served", False):
                return []
            self._served = True
            return [{"reference": {"repo": "github.com/a/x", "commit": "a" * 40}}]

    monkeypatch.setattr(server, "HubClient", _Hub)
    monkeypatch.setattr(server, "verify_program",
                        lambda *a, **kw: seen.update(kw) or "succeeded")
    server.main(["--hub", "http://h", "--token", "vt", "--once",
                 "--max-parallel-evals", "6"])
    assert seen["max_parallel_evals"] == 6


# ---------------------------------------------------------------------------
# Hub submission robustness. A verifier run is a batch job of hours: losing a
# completed 15-episode identity because a write exceeded httpx's 5s default --
# against a hub concurrently serving verification traffic -- is unacceptable,
# and the failure it logged ("timed out", no type) was undiagnosable.
# ---------------------------------------------------------------------------


def test_worker_gives_the_hub_client_a_generous_explicit_timeout(monkeypatch):
    """httpx defaults to 5s. A POST of 15 atoms to a busy hub can exceed
    that, and the completed episodes are then thrown away."""
    seen = {}

    class _C(_StubHub):
        def __init__(self, *a, **kw):
            seen["args"], seen["kwargs"] = a, kw

    monkeypatch.setattr(server, "HubClient", _C)
    monkeypatch.setattr(server, "compute_hidden_baseline", lambda *a, **kw: "succeeded")
    with pytest.raises(SystemExit):
        server.main(["--hub", "http://h", "--token", "vt", "--baseline"])
    assert seen["kwargs"].get("timeout") is not None, "worker must not inherit httpx's 5s default"
    assert seen["kwargs"]["timeout"] >= 30


def test_failure_log_names_the_exception_type():
    """`str(e)` alone gave a bare 'timed out' with no clue whether it was the
    evaluator or the hub, or which timeout fired."""
    lines = []

    class _C(_Client):
        def post_verify_baseline(self, token, *, evidence, secret_fingerprint):
            raise TimeoutError("timed out")

    compute_hidden_baseline(
        _C(), "vt", CONFIG, "roots/autoascend",
        eval_fn=lambda tree, spec, image, *, now, secret, **kw: _evidence(spec),
        now_fn=lambda: "t", identities=("val-dwa-law-fem",), log=lines.append)
    failure = [ln for ln in lines if "failed" in ln]
    assert failure and "TimeoutError" in failure[0], failure


def test_progress_lines_are_flushed_not_buffered(capsys):
    """An hours-long batch job whose progress sits in a 4KB stdout buffer is
    unmonitorable -- `tail -f` and `journalctl` show nothing until it exits.
    The default logger must flush each line."""
    import io

    stream = io.StringIO()
    flushes = []
    stream.flush = lambda: flushes.append(len(stream.getvalue()))

    from nethackers.worker.verify import _log

    _log("baseline: something happened", stream=stream)
    assert "baseline: something happened" in stream.getvalue()
    assert flushes, "logger must flush, or progress is invisible until exit"
