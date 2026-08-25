from nethackers.harness.loop import _causes


class _R:
    def __init__(self, cause):
        self.cause_of_death = cause


def test_causes_counts_and_drops_none():
    results = [_R("killed by a jackal"), _R("killed by a jackal"),
               _R("starved to death"), _R(None)]
    assert _causes(results) == {"killed by a jackal": 2, "starved to death": 1}


def test_causes_empty():
    assert _causes([]) == {}
