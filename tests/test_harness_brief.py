# tests/test_harness_brief.py
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.brief import build_brief


def _ev(progresses_and_ends):
    results = tuple(
        TrajectoryResult(trajectory_id=i, status="completed", progress=p, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status=e, error=None,
                         wall_seconds=0.1, character="val-dwa-law-fem", milestone=None)
        for i, (p, e) in enumerate(progresses_and_ends))
    return Evidence.from_results(
        solution_digest="sha256:x",
        objective=Objective(character="val-dwa-law-fem"),
        evaluator_image="img", results=results, created_at="t")


def test_brief_has_goal_scorecard_and_entrypoint_rule():
    ev = _ev([(0.4, "died"), (0.2, "starved"), (0.4, "died")])
    brief = build_brief("val-dwa-law-fem", "val-dwa-law-fem", ev)
    assert "val-dwa-law-fem" in brief
    assert "make_agent" in brief and "bot.py" in brief    # entrypoint rule
    assert "died" in brief and "starved" in brief          # failure tally
    assert "0.3" in brief or "0.33" in brief               # mean progression shown
    # curated, not a dump: no full episode JSON, stays short
    assert len(brief) < 2000
