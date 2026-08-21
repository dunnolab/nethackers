# tests/test_harness_brief.py
from nethackers.contracts.models import Evidence, Objective, TrajectoryResult
from nethackers.harness.brief import build_brief


def _evidence(mean, episodes, tally):
    ends = [end for end, count in tally.items() for _ in range(count)]
    ends += ["unknown"] * (episodes - len(ends))
    results = tuple(
        TrajectoryResult(trajectory_id=i, status="completed", progress=mean, ascended=False,
                         steps=1, turns=1, max_depth=1, end_status=e, error=None,
                         wall_seconds=0.1, character="val-dwa-law-fem", milestone=None)
        for i, e in enumerate(ends))
    return Evidence.from_results(
        solution_digest="sha256:x",
        objective=Objective(character="val-dwa-law-fem"),
        evaluator_image="img", results=results, created_at="t")


def test_brief_targets_progression_not_proxies():
    b = build_brief("ascend", "val-wiz", _evidence(mean=12.3, episodes=8, tally={"starved": 5}))
    assert "progression" in b.lower()
    assert "reach deeper" not in b and "survive longer" not in b   # old proxy line gone
    assert "objective 'ascend'" in b                               # objective_name is used
    assert "12.3" in b                                             # mean progression shown
    assert "starved" in b                                          # outcome tally shown


def test_brief_has_antigaming_and_generalization_and_gate():
    b = build_brief("ascend", "val-wiz", _evidence(mean=1.0, episodes=4, tally={}))
    assert "held-out" in b.lower()
    assert "seed fingerprint" in b.lower() or "fingerprint" in b.lower()   # named exploit #1
    assert "scorer/nle quirks" in b.lower()                                # named exploit #2
    assert "make_agent" in b                                              # frozen-skeleton gate
    assert "hypothesis" in b.lower()                                      # focused-change comment


def test_wiki_line_is_conditional():
    assert "reference" not in build_brief("o", "c", _evidence(1, 1, {})).lower()
    b = build_brief("o", "c", _evidence(1, 1, {}), wiki_path="/knowledge/nethack")
    assert "/knowledge" in b.lower()


def test_training_seeds_render_when_given():
    b = build_brief("o", "c", _evidence(1, 1, {}), training_seeds=[3, 17, 42])
    assert "3, 17, 42" in b
