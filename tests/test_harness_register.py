# tests/test_harness_register.py
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.register import register_win


class _FakeHub:
    def __init__(self):
        self.calls = []

    def register(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


def _ev():
    r = TrajectoryResult(trajectory_id=0, status="completed", progress=0.5, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status="died", error=None,
                         wall_seconds=0.1, character="val-dwa-law-fem", milestone=None)
    return Evidence.from_results(solution_digest="sha256:" + "ab" * 32,
                                 objective=Objective(character="val-dwa-law-fem"),
                                 evaluator_image="img", results=(r,), created_at="t")


def test_register_win_is_noop_in_m1():
    # M1: the evolve loop accepts a validation win as its new local elite but
    # does NOT auto-publish to the hub -- link-only registration is a deliberate,
    # manual `nethackers register` step (see harness/register.py).
    hub = _FakeHub()
    manifest = {"schema": "nethackers.solution/v1", "name": "c", "root": ".",
                "parents": [], "influences": [], "entrypoint": "bot.py"}
    result = register_win(hub, token="dev-token", owner="dev", child_manifest=manifest,
                          evidence=_ev(), parent_digest="sha256:PARENT")
    assert result is None
    assert hub.calls == []             # hub is NOT contacted in M1
    assert manifest["parents"] == []   # caller's dict untouched
