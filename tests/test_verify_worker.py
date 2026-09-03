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
