# tests/test_harness_register.py
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.register import register_win, register_win_slices


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
    assert manifest["parents"] == []                            # caller's dict untouched
    assert call["evidence"]["solution_digest"] == "sha256:" + "ab" * 32

# --- generalist (per-identity slices) -------------------------------------

def _tr(seed, character, progress=0.3):
    return TrajectoryResult(trajectory_id=seed, status="completed", progress=progress,
                            ascended=False, steps=1, turns=1, max_depth=1, end_status="died",
                            error=None, wall_seconds=0.1, character=character, milestone=None)

def _union_evidence():
    results = [_tr(s, c) for c in ("wiz-elf-cha-mal", "wiz-orc-cha-mal") for s in range(15)]
    return Evidence.from_results(
        solution_digest="sha256:" + "ab" * 32,
        objective=Objective(character=None, seed_set="wiz"),
        evaluator_image="img", results=results, created_at="t")

def test_register_win_slices_makes_one_call_per_identity_with_correct_batch():
    hub = _FakeHub()
    manifest = {"schema": "nethackers.solution/v1", "name": "c", "root": ".",
                "parents": [], "influences": [], "entrypoint": "bot.py"}
    register_win_slices(
        hub, token="dev-token", owner="dev", child_manifest=manifest,
        evidence=_union_evidence(), identities=["wiz-elf-cha-mal", "wiz-orc-cha-mal"],
        parent_digest="sha256:PARENT")
    assert len(hub.calls) == 2
    by_ident = {c["evidence"]["objective"]["seed_set"]: c["evidence"] for c in hub.calls}
    assert set(by_ident) == {"wiz-elf-cha-mal", "wiz-orc-cha-mal"}
    for ident, ev in by_ident.items():
        submitted = {(r["trajectory_id"], r["character"]) for r in ev["results"]}
        assert submitted == {(s, ident) for s in range(15)}
