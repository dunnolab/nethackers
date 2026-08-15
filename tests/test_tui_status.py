from nethackers.tui.status import EvolveConfig, format_status

CFG = EvolveConfig(objective="val-dwa-law-fem", backend="claude",
                   iterations=3, token_budget=40_000)


def _state(phase, **kw):
    base = {"phase": phase, "iteration": 2, "baseline_dev": 0.070, "baseline_held": 0.050,
            "best_dev": 0.089, "best_held": 0.061, "wins": 1, "tokens": 0, "detail": ""}
    base.update(kw)
    return base


def test_mutating_shows_tokens_and_clock():
    l1, l2 = format_status(CFG, _state("mutating"), live_tokens=12_480, elapsed_s=41)
    assert l1 == "MUTATING iter 2/3 · 12.5k/40.0k tok · 0:41"
    assert l2 == "best dev 0.089 (base 0.070) · held 0.061 · 1 win"


def test_evaluating_shows_episode_and_mean():
    l1, _ = format_status(CFG, _state("evaluating-dev"), eval_step=(3, 8, 0.074))
    assert l1 == "EVALUATING dev · iter 2/3 · ep 3/8 · x̄ 0.074"


def test_cold_start_and_done_and_rejected():
    assert format_status(CFG, _state("cold-start", iteration=0))[0] == \
        "COLD START · scoring baseline …"
    assert format_status(CFG, _state("done", wins=2))[0] == "DONE · 2/3 registered"
    assert format_status(CFG, _state("rejected", detail="no dev gain"))[0] == \
        "✗ iter 2/3 · no dev gain"


def test_pluralizes_wins():
    assert format_status(CFG, _state("mutating", wins=0))[1].endswith("· 0 wins")
    assert format_status(CFG, _state("mutating", wins=2))[1].endswith("· 2 wins")


def test_migrated_phase_shows_source():
    l1, _ = format_status(CFG, _state("migrated", detail="alice/sha256:abcd1"))
    assert l1 == "↥ MIGRATED iter 2/3 ← alice/sha256:abcd1"
