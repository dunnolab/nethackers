# tests/test_cli_selector.py
"""``evolve`` objective validation: Task 9 (generalist objectives) has the
CLI accept whatever ``nethackers.hub.selector.resolve`` accepts -- a full
identity, a role, a comma list, a glob -- not just the exact hub catalog
``eval`` still requires. This module covers the rejection path only: any
token ``resolve`` can't make sense of -- including ``random``/``all``,
retired (Task A1): both used to resolve to a real (if, for ``all``, evolve-
disallowed) kind, but now hit the exact same unknown-objective rejection as
``definitely-not-a-build``. Must fail with rc 2 and one clean message
*before* ``sandbox_preflight``/``image_present``/``prepare_evolve`` ever
run -- so no docker, no run dir, and no need to mock them green the way
test_cli_evolve.py does. Valid role/comma-list/glob wiring is exercised at
the selector layer (tests/hub/test_selector.py) and the harness layer, not
here.
"""
from nethackers import cli


def _refuse_docker(monkeypatch):
    # If the early-return regresses -- validation moved past
    # sandbox_preflight, or dropped entirely -- these fire and the test
    # fails loudly, instead of quietly shelling out to a real container
    # runtime.
    def _boom(name):
        def _raise(*a, **kw):
            raise AssertionError(f"evolve must reject a bad objective before {name}")
        return _raise
    monkeypatch.setattr(cli, "sandbox_preflight", _boom("sandbox_preflight"))
    monkeypatch.setattr(cli, "image_present", _boom("image_present"))
    monkeypatch.setattr(cli, "prepare_evolve", _boom("prepare_evolve"))


def test_evolve_rejects_unresolvable_selector_cleanly(capsys, monkeypatch):
    _refuse_docker(monkeypatch)
    rc = cli._run(["evolve", "definitely-not-a-build", "--seed", "roots/autoascend"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "unknown" in (captured.out + captured.err).lower()
    assert "Traceback" not in captured.out and "Traceback" not in captured.err


def test_evolve_rejects_all_as_leaderboard_view_not_a_target(capsys, monkeypatch):
    # "all" is retired (Task A1): resolve() itself rejects it now, the same
    # as any other unrecognized token -- evolve's former special case (which
    # steered users to a "leaderboard view, not an evolve target" hint,
    # premised on "all" still resolving to a real, disallowed kind) is gone.
    # Kept as its own test -- a regression guard that "all" specifically
    # stays rejected, not silently accepted now that it's no longer special-
    # cased.
    _refuse_docker(monkeypatch)
    rc = cli._run(["evolve", "all", "--seed", "roots/autoascend"])
    assert rc == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "unknown objective 'all'" in combined.lower()
    assert "Traceback" not in combined
    # random/all are retired -- the hint must not suggest either as a
    # fallback objective to try instead.
    assert "'random'" not in combined
