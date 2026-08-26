from nethackers.arena.progress import ACHIEVEMENTS
from nethackers.hub.views.milestones import deepest_milestone


def test_deepest_milestone_picks_the_deepest_by_achievement_value():
    labels = ["Dlvl:2", "Dlvl:5", "Dlvl:3"]
    expected = max(labels, key=lambda m: ACHIEVEMENTS[m])
    assert deepest_milestone(labels) == expected
    # and it is genuinely a deeper-beats-shallower pick, not first/last
    assert ACHIEVEMENTS[expected] == max(ACHIEVEMENTS[m] for m in labels)


def test_deepest_milestone_ignores_none_and_empty():
    assert deepest_milestone([None, "Dlvl:2", None]) == "Dlvl:2"
    assert deepest_milestone([]) is None
    assert deepest_milestone([None, None]) is None
