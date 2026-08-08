from nethackers.arena.aggregate import aggregate
from nethackers.contracts.models import TrajectoryResult


def _r(p, asc, status="completed"):
    return TrajectoryResult(0, status, p, asc, 1, 1, 1, None, None, 0.0)


def test_aggregate_mean_ascensions_median():
    out = aggregate([_r(0.2, False), _r(1.0, True), _r(0.6, False)])
    assert abs(out["mean_progress"] - 0.6) < 1e-9
    assert out["ascensions"] == 1
    assert out["median_progress"] == 0.6
