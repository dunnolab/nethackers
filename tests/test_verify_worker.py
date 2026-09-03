import nethackers.worker.server as server
from nethackers._image_pins import ARENA_IMAGE
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.worker.verify import verified_identity_spec, verify_program

CONFIG = {"secret": "hidden-key", "seeds": [4839201, 1029384]}
REF = {"repo": "github.com/a/x", "commit": "a" * 40}


def _evidence(spec):
    results = tuple(
        TrajectoryResult(trajectory_id=sd, status="completed", progress=0.4, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character=char, milestone=None)
        for sd, char in spec.batch
    )
    return Evidence.from_results(
        solution_digest="sid", objective=Objective(character=None, seed_set="v"),
        evaluator_image=ARENA_IMAGE, results=results, created_at="t")


class _Client:
    def __init__(self):
        self.verified = []
        self.attempts = []

    def post_verify(self, token, *, reference, evidence, secret_fingerprint):
        self.verified.append(evidence)
        return {"inserted": len(evidence["results"])}

    def post_verify_attempt(self, token, **kw):
        self.attempts.append(kw)
        return {"ok": True}


def test_verified_identity_spec_uses_config_seeds():
    spec = verified_identity_spec("val-dwa-law-fem", (4839201, 1029384))
    assert spec.batch == ((4839201, "val-dwa-law-fem"), (1029384, "val-dwa-law-fem"))


def test_verify_program_submits_per_identity_and_reports_success():
    client = _Client()
    status = verify_program(client, "vt", CONFIG, REF, pull_fn=lambda ref, dest: dest,
                            eval_fn=lambda tree, spec, image, *, now, secret: _evidence(spec),
                            now_fn=lambda: "t", identities=("val-dwa-law-fem", "wiz-elf-cha-mal"))
    assert status == "succeeded"
    assert len(client.verified) == 2                      # one submit per identity
    assert client.attempts[-1]["status"] == "succeeded"


def test_verify_program_reports_failure_on_eval_error():
    client = _Client()
    def boom(*a, **k): raise RuntimeError("container died")
    status = verify_program(client, "vt", CONFIG, REF, pull_fn=lambda ref, dest: dest,
                            eval_fn=boom, now_fn=lambda: "t", identities=("val-dwa-law-fem",))
    assert status == "failed"
    assert client.attempts[-1]["failure_kind"] == "crashed"


def test_main_one_shot_verifies_given_program(monkeypatch):
    seen = {}
    def _fake_verify(client, token, config, reference, **kw):
        seen["ref"] = reference
        return "succeeded"
    monkeypatch.setattr(server, "verify_program", _fake_verify)
    class _C:
        def __init__(self, *a, **k): pass
        def get_verify_config(self, token): return {"secret": "s", "seeds": [1]}
    monkeypatch.setattr(server, "HubClient", _C)
    server.main(["--hub", "http://h", "--token", "vt", "github.com/a/x@" + "a" * 40])
    assert seen["ref"] == {"repo": "github.com/a/x", "commit": "a" * 40}


def test_daemon_processes_candidates_then_stops_when_idle(monkeypatch):
    calls = {"verify": 0}
    class _C:
        def __init__(self, *a, **k): self._served = False
        def get_verify_config(self, token): return {"secret": "s", "seeds": [1]}
        def get_verify_candidates(self, token, *, limit=8):
            if self._served:
                return []
            self._served = True
            return [{"reference": {"repo": "github.com/a/x", "commit": "a" * 40}}]
    monkeypatch.setattr(server, "HubClient", _C)
    def _fake_verify(*a, **k):
        calls["verify"] += 1
        return "succeeded"
    monkeypatch.setattr(server, "verify_program", _fake_verify)
    # --once processes at most one candidate pass then returns
    server.main(["--hub", "http://h", "--token", "vt", "--once"])
    assert calls["verify"] == 1


def test_daemon_absorbs_verify_program_exception_and_completes_pass(monkeypatch):
    class _C:
        def __init__(self, *a, **k): self._served = False
        def get_verify_config(self, token): return {"secret": "s", "seeds": [1]}
        def get_verify_candidates(self, token, *, limit=8):
            if self._served:
                return []
            self._served = True
            return [{"reference": {"repo": "github.com/a/x", "commit": "a" * 40}}]
    monkeypatch.setattr(server, "HubClient", _C)
    def _boom(*a, **k):
        raise RuntimeError("hub down")
    monkeypatch.setattr(server, "verify_program", _boom)
    # A verify_program exception (e.g. its own attempt-report call hitting a
    # hub blip) must be absorbed, not propagate out of main() and kill the
    # worker -- returning normally, instead of raising, IS the assertion.
    server.main(["--hub", "http://h", "--token", "vt", "--once"])


def test_daemon_backs_off_on_hub_error_then_recovers(monkeypatch):
    sleeps = []
    monkeypatch.setattr(server.time, "sleep", lambda s: sleeps.append(s))
    calls = {"verify": 0}
    class _C:
        def __init__(self, *a, **k): self._fails = 2
        def get_verify_config(self, token):
            if self._fails:
                self._fails -= 1
                raise RuntimeError("hub down")
            return {"secret": "s", "seeds": [1]}
        def get_verify_candidates(self, token, *, limit=8): return []
    monkeypatch.setattr(server, "HubClient", _C)
    def _fake_verify(*a, **k):
        calls["verify"] += 1
    monkeypatch.setattr(server, "verify_program", _fake_verify)
    server.main(["--hub", "http://h", "--token", "vt", "--once"])
    assert sleeps == [10.0, 20.0]  # exponential: base 10s, doubling per failed fetch
    assert calls["verify"] == 0    # recovered with no candidates -> --once returns
