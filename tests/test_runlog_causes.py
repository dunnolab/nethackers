from nethackers.harness.loop import IterationResult
from nethackers.harness.runlog import metric_record


def test_metric_record_includes_causes():
    r = IterationResult(True, "registered", dev_fitness=0.6,
                        causes={"killed by a jackal": 3})
    assert metric_record(1, r)["causes"] == {"killed by a jackal": 3}


def test_metric_record_causes_defaults_to_none():
    r = IterationResult(False, "gate:smoke")
    assert metric_record(1, r)["causes"] is None
