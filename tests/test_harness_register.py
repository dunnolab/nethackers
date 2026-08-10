# tests/test_harness_register.py
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.register import register_win


class _FakeHub:
    def __init__(self):
        self.calls = []

    def register(self, *, token, reference, manifest, evidence):
        self.calls.append({"token": token, "reference": reference,
                           "manifest": manifest, "evidence": evidence})
        return {"solution_digest": evidence["solution_digest"]}


def _ev():
    r = TrajectoryResult(trajectory_id=0, status="completed", progress=0.5, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character="val-dwa-law-fem", milestone=None)
    return Evidence.from_results(solution_digest="sha256:" + "ab" * 32,
                                 objective=Objective(character="val-dwa-law-fem"),
                                 evaluator_image="img", results=(r,), created_at="t")


def test_register_win_builds_payload_and_records_lineage():
    hub = _FakeHub()
    manifest = {"schema": "nethackers.solution/v1", "name": "c", "root": ".",
                "parents": [], "influences": [], "entrypoint": "bot.py"}
    register_win(hub, token="dev-token", owner="dev", child_manifest=manifest,
                 evidence=_ev(), parent_digest="sha256:PARENT")
    call = hub.calls[0]
    assert call["token"] == "dev-token"
    assert call["reference"]["repo"] == "github.com/dev/nethacker-runs"
    assert len(call["reference"]["commit"]) == 40
    assert call["manifest"]["parents"] == ["sha256:PARENT"]     # lineage recorded
    assert call["evidence"]["solution_digest"] == "sha256:" + "ab" * 32
