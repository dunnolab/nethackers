"""Resolving + validating the directory a run is scored from.

Two defects these cover, both hit by a `pip install nethackers` user following
the README verbatim:

1. The wheel shipped no AutoAscend, while every doc says `--seed roots/autoascend`.
2. A solution root that isn't there scored 0.0 at exit code 0 -- docker's bind
   mount creates the absent host directory, so all 15 episodes ran against an
   empty folder and the batch reported a real-looking number.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nethackers import solution_root as sr
from nethackers.solution_root import (
    SolutionRootError,
    check_solution_root,
    require_solution_root,
    resolve_solution_root,
)


def _pip_user_cwd(base: Path) -> Path:
    """A directory with no roots/ of its own -- what a pip user's cwd looks
    like. Without this the test reads THIS repo's real roots/autoascend (the
    suite runs from the checkout) and never exercises the packaged fallback.
    """
    cwd = base / "cwd"
    cwd.mkdir(exist_ok=True)
    return cwd


def _tree(base: Path, name: str = "autoascend") -> Path:
    """A minimally valid solution root: a directory holding a bot.py."""
    root = base / name
    root.mkdir(parents=True)
    (root / "bot.py").write_text("def make_agent(): ...\n")
    return root


# --- resolution -------------------------------------------------------------

def test_a_path_that_exists_is_returned_untouched(tmp_path, monkeypatch):
    """A real directory always wins, so a checkout is unaffected and a user's
    own roots/autoascend is never shadowed by the packaged copy."""
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", _tree(tmp_path / "pkg").parent)
    monkeypatch.chdir(tmp_path)
    mine = _tree(tmp_path / "roots")
    assert resolve_solution_root("roots/autoascend") == Path("roots/autoascend")
    assert (Path("roots/autoascend").resolve()) == mine.resolve()


def test_documented_path_falls_back_to_the_packaged_tree(tmp_path, monkeypatch):
    """`roots/autoascend` is what all five docs tell users to pass. Off a
    checkout it resolves to nothing, so it must find the wheel's own copy."""
    packaged = _tree(tmp_path / "pkg")
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", packaged.parent)
    monkeypatch.chdir(_pip_user_cwd(tmp_path))
    assert resolve_solution_root("roots/autoascend") == packaged


def test_bare_name_resolves_too(tmp_path, monkeypatch):
    packaged = _tree(tmp_path / "pkg")
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", packaged.parent)
    monkeypatch.chdir(_pip_user_cwd(tmp_path))
    assert resolve_solution_root("autoascend") == packaged


def test_trailing_slash_and_dot_prefix_still_resolve(tmp_path, monkeypatch):
    packaged = _tree(tmp_path / "pkg")
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", packaged.parent)
    monkeypatch.chdir(_pip_user_cwd(tmp_path))
    assert resolve_solution_root("./roots/autoascend/") == packaged


def test_a_checkout_tree_beats_the_packaged_copy(tmp_path, monkeypatch):
    """Both present (pip-installed inside a checkout): the developer's own tree
    is the one they mean."""
    packaged = _tree(tmp_path / "pkg")
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", packaged.parent)
    monkeypatch.chdir(tmp_path)
    checkout = _tree(tmp_path / "roots")
    assert resolve_solution_root("autoascend").resolve() == checkout.resolve()


def test_an_unknown_missing_path_is_not_masked(tmp_path, monkeypatch):
    """The fallback is for AutoAscend's canonical names only. A typo must stay
    a typo -- silently substituting a different solution is the 0.0 bug again."""
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", _tree(tmp_path / "pkg").parent)
    assert resolve_solution_root("my-bot") == Path("my-bot")


# --- validation -------------------------------------------------------------

def test_a_good_root_has_no_problem(tmp_path):
    assert check_solution_root(_tree(tmp_path)) is None


def test_a_missing_root_names_the_path_the_user_typed(tmp_path):
    problem = check_solution_root(tmp_path / "nope")
    assert problem is not None
    assert "nope" in problem


def test_a_directory_without_a_bot_is_rejected(tmp_path):
    (tmp_path / "empty").mkdir()
    problem = check_solution_root(tmp_path / "empty")
    assert problem is not None and "bot.py" in problem


def test_a_file_is_not_a_solution_root(tmp_path):
    f = tmp_path / "bot.py"
    f.write_text("x")
    assert check_solution_root(f) is not None


def test_require_raises_with_a_finished_sentence(tmp_path):
    with pytest.raises(SolutionRootError) as caught:
        require_solution_root(tmp_path / "nope")
    # A user-facing sentence, not a repr -- cli prints str(exc) directly.
    assert "nope" in str(caught.value)


def test_require_returns_the_resolved_path(tmp_path, monkeypatch):
    packaged = _tree(tmp_path / "pkg")
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", packaged.parent)
    monkeypatch.chdir(_pip_user_cwd(tmp_path))
    assert require_solution_root("autoascend") == packaged


# --- the regression: eval_batch must refuse, not score 0.0 -------------------

def test_eval_batch_refuses_a_missing_root_before_touching_docker(tmp_path):
    """The bug this whole module exists for. Docker's bind mount CREATES the
    absent host path, so this used to run the full batch against an empty
    directory and report mean_progress 0.0 at exit code 0."""
    from nethackers.eval.runner import eval_batch
    from nethackers.hub.objectives import CATALOG

    calls = []

    with pytest.raises(SolutionRootError) as caught:
        eval_batch(tmp_path / "typo", CATALOG["val-dwa-law-fem"], "img",
                   now="t", runner=lambda *a, **k: calls.append(a),
                   image_digest_resolver=lambda i: i)
    assert "typo" in str(caught.value)
    assert calls == [], "must fail before any container starts"


def test_eval_batch_accepts_a_real_root(tmp_path, monkeypatch):
    """The guard must not reject what it should let through: the same call with
    a bot.py present gets past validation and on to the runner."""
    from nethackers.eval.runner import eval_batch
    from nethackers.hub.objectives import CATALOG

    reached = []

    def runner(*args, **kwargs):
        reached.append(args)
        raise SystemExit  # stop before results.json -- we only need "it got here"

    with pytest.raises(SystemExit):
        eval_batch(_tree(tmp_path, "mine"), CATALOG["val-dwa-law-fem"], "img",
                   now="t", runner=runner, image_digest_resolver=lambda i: i)
    assert reached, "a valid root must reach the container"


# --- the TUI offered the path it had just failed to find ---------------------

def test_evolve_form_offers_a_seed_that_exists(tmp_path, monkeypatch):
    """`_seed_roots` used to fall back to the literal string 'roots/autoascend'
    when it found nothing under ./roots -- i.e. off a checkout it presented, as
    the only choice, the path it had just searched for and missed."""
    from nethackers.tui.screens import evolve_form

    packaged = _tree(tmp_path / "pkg")
    monkeypatch.setattr(sr, "PACKAGED_ROOTS", packaged.parent)
    monkeypatch.chdir(_pip_user_cwd(tmp_path))

    offered = evolve_form._seed_roots()

    assert offered == [packaged.as_posix()]
    assert check_solution_root(Path(offered[0])) is None


def test_evolve_form_still_prefers_local_roots(tmp_path, monkeypatch):
    """A checkout's own roots/ keeps winning -- the fallback is only a fallback."""
    from nethackers.tui.screens import evolve_form

    monkeypatch.setattr(sr, "PACKAGED_ROOTS", _tree(tmp_path / "pkg").parent)
    monkeypatch.chdir(tmp_path)
    mine = tmp_path / "roots" / "mybot"
    mine.mkdir(parents=True)
    (mine / "nethackers.solution.json").write_text("{}")

    assert evolve_form._seed_roots() == ["roots/mybot"]
