from nethackers.contracts.models import Evidence, Objective, TrajectoryResult


def _tr(pid, progress, ascended=False):
    return TrajectoryResult(pid, "completed", progress, ascended, 10, 5, 3, None, None, 0.1)


def test_evidence_aggregates_mean_progress_and_ascensions():
    obj = Objective(None, 1000, 100, 5.0, "public-2")
    ev = Evidence.from_results(
        solution_digest="sha256:abc", objective=obj, evaluator_image="img@sha256:x",
        results=[_tr(0, 0.2), _tr(1, 0.6, ascended=True)], created_at="2026-08-08T00:00:00Z")
    assert ev.tier == "self-reported"
    assert ev.episodes == 2
    assert abs(ev.mean_progress - 0.4) < 1e-9
    assert ev.ascensions == 1
    assert Evidence.from_dict(ev.to_dict()) == ev
